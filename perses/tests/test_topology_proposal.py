import simtk.openmm.app as app
import simtk.openmm as openmm
import simtk.unit as unit
from pkg_resources import resource_filename
import numpy as np
import os
try:
    from urllib.request import urlopen
    from io import StringIO
except:
    from urllib2 import urlopen
    from cStringIO import StringIO
from nose.plugins.attrib import attr

from openmmtools.constants import kB
from perses.utils.openeye import OEMol_to_omm_ff, smiles_to_oemol
from perses.utils.charge_changing import get_water_indices
from perses.rjmc.topology_proposal import SmallMoleculeSetProposalEngine
from perses.rjmc import topology_proposal
from collections import defaultdict
from openmmforcefields.generators import SystemGenerator
from openff.toolkit.topology import Molecule
from openmoltools.forcefield_generators import generateOEMolFromTopologyResidue

#default arguments for SystemGenerators
barostat = None
forcefield_files = ['amber14/protein.ff14SB.xml', 'amber14/tip3p.xml']
forcefield_kwargs = {'removeCMMotion': False, 'ewaldErrorTolerance': 1e-4, 'constraints' : app.HBonds, 'hydrogenMass' : 3 * unit.amus}
nonperiodic_forcefield_kwargs = {'nonbondedMethod': app.NoCutoff}
small_molecule_forcefield = 'gaff-2.11'

temperature = 300*unit.kelvin
# Compute kT and inverse temperature.
kT = kB * temperature
beta = 1.0 / kT
ENERGY_THRESHOLD = 1e-6
PROHIBITED_RESIDUES = ['CYS']

running_on_github_actions = os.environ.get('GITHUB_ACTIONS', None) == 'true'

def test_small_molecule_proposals():
    """
    Make sure the small molecule proposal engine generates molecules
    """
    import openeye.oechem as oechem

    list_of_smiles = ['CCCC','CCCCC','CCCCCC']
    list_of_mols = []
    for smi in list_of_smiles:
        mol = smiles_to_oemol(smi)
        list_of_mols.append(mol)
    molecules = [Molecule.from_openeye(mol) for mol in list_of_mols]
    stats_dict = defaultdict(lambda: 0)
    system_generator = SystemGenerator(forcefields = forcefield_files, barostat=barostat, forcefield_kwargs=forcefield_kwargs, nonperiodic_forcefield_kwargs=nonperiodic_forcefield_kwargs,
                                         small_molecule_forcefield = small_molecule_forcefield, molecules=molecules, cache=None)
    proposal_engine = topology_proposal.SmallMoleculeSetProposalEngine(list_of_mols, system_generator)
    initial_system, initial_positions, initial_topology,  = OEMol_to_omm_ff(list_of_mols[0], system_generator)

    proposal = proposal_engine.propose(initial_system, initial_topology)

    for i in range(50):
        # Positions are ignored here, and we don't want to run the geometry engine
        new_proposal = proposal_engine.propose(proposal.old_system, proposal.old_topology)
        stats_dict[new_proposal.new_chemical_state_key] += 1
        # Check that the molecule it generated is actually the smiles we expect
        matching_molecules = [res for res in proposal.new_topology.residues() if res.name=='MOL']
        if len(matching_molecules) != 1:
            raise ValueError("More than one residue with the same name!")
        mol_res = matching_molecules[0]
        oemol = generateOEMolFromTopologyResidue(mol_res)
        smiles = SmallMoleculeSetProposalEngine.canonicalize_smiles(oechem.OEMolToSmiles(oemol))
        assert smiles == proposal.new_chemical_state_key
        proposal = new_proposal

def load_pdbid_to_openmm(pdbid):
    """
    create openmm topology without pdb file
    lifted from pandegroup/pdbfixer
    """
    url = 'http://www.rcsb.org/pdb/files/%s.pdb' % pdbid
    file = urlopen(url)
    contents = file.read().decode('utf-8')
    file.close()
    file = StringIO(contents)

    if _guessFileFormat(file, url) == 'pdbx':
        pdbx = app.PDBxFile(contents)
        topology = pdbx.topology
        positions = pdbx.positions
    else:
        pdb = app.PDBFile(file)
        topology = pdb.topology
        positions = pdb.positions

    return topology, positions

def _guessFileFormat(file, filename):
    """
    Guess whether a file is PDB or PDBx/mmCIF based on its filename and contents.
    authored by pandegroup
    """
    filename = filename.lower()
    if '.pdbx' in filename or '.cif' in filename:
        return 'pdbx'
    if '.pdb' in filename:
        return 'pdb'
    for line in file:
        if line.startswith('data_') or line.startswith('loop_'):
            file.seek(0)
            return 'pdbx'
        if line.startswith('HEADER') or line.startswith('REMARK') or line.startswith('TITLE '):
            file.seek(0)
            return 'pdb'
    file.seek(0)
    return 'pdb'

def create_simple_protein_system_generator():
    from openmmforcefields.generators import SystemGenerator
    barostat = None
    forcefield_files = ['amber14/protein.ff14SB.xml', 'amber14/tip3p.xml']
    forcefield_kwargs = {'removeCMMotion': False, 'ewaldErrorTolerance': 1e-4, 'constraints' : app.HBonds, 'hydrogenMass' : 3 * unit.amus}
    nonperiodic_forcefield_kwargs={'nonbondedMethod': app.NoCutoff}

    system_generator = SystemGenerator(forcefields = forcefield_files, barostat=barostat, forcefield_kwargs=forcefield_kwargs, nonperiodic_forcefield_kwargs=nonperiodic_forcefield_kwargs,
                                         small_molecule_forcefield = 'gaff-2.11', molecules=None, cache=None)
    return system_generator

def create_insulin_topology_engine(chain_id = 'A', allowed_mutations = None, pdbid = "2HIU"):
    import perses.rjmc.topology_proposal as topology_proposal

    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)
    for chain in modeller.topology.chains():
        pass
    modeller.delete([chain])
    system_generator = create_simple_protein_system_generator()
    system = system_generator.create_system(modeller.topology)

    pm_top_engine = topology_proposal.PointMutationEngine(modeller.topology, system_generator, chain_id, allowed_mutations=allowed_mutations)

    return pm_top_engine, system, topology, modeller.positions


def generate_atp(phase = 'vacuum'):
    """
    modify the AlanineDipeptideVacuum test system to be parametrized with amber14ffsb in vac or solvent (tip3p)
    """
    import openmmtools.testsystems as ts
    from openmmforcefields.generators import SystemGenerator
    atp = ts.AlanineDipeptideVacuum(constraints = app.HBonds, hydrogenMass = 3 * unit.amus)


    forcefield_files = ['gaff.xml', 'amber14/protein.ff14SB.xml', 'amber14/tip3p.xml']

    if phase == 'vacuum':
        barostat = None
        system_generator = SystemGenerator(forcefield_files,
                                       barostat=barostat,
                                       forcefield_kwargs={'removeCMMotion': False,
                                                            'ewaldErrorTolerance': 1e-4,
                                                            'constraints' : app.HBonds,
                                                            'hydrogenMass' : 3 * unit.amus},
                                        nonperiodic_forcefield_kwargs={'nonbondedMethod': app.NoCutoff},
                                        small_molecule_forcefield='gaff-2.11',
                                        molecules=None,
                                        cache=None)

        atp.system = system_generator.create_system(atp.topology) # Update the parametrization scheme to amberff14sb

    elif phase == 'solvent':
        barostat = openmm.MonteCarloBarostat(1.0 * unit.atmosphere, 300 * unit.kelvin, 50)

        system_generator = SystemGenerator(forcefield_files,
                                   barostat=barostat,
                                   forcefield_kwargs={'removeCMMotion': False,
                                                        'ewaldErrorTolerance': 1e-4,
                                                        'constraints' : app.HBonds,
                                                        'hydrogenMass' : 3 * unit.amus},
                                    periodic_forcefield_kwargs={'nonbondedMethod': app.PME},
                                    small_molecule_forcefield='gaff-2.11',
                                    molecules=None,
                                    cache=None)

    if phase == 'solvent':
        modeller = app.Modeller(atp.topology, atp.positions)
        modeller.addSolvent(system_generator.forcefield, model='tip3p', padding=11*unit.angstroms, ionicStrength=0.15*unit.molar)
        solvated_topology = modeller.getTopology()
        solvated_positions = modeller.getPositions()

        # Canonicalize the solvated positions: turn tuples into np.array
        atp.positions = unit.quantity.Quantity(value=np.array([list(atom_pos) for atom_pos in solvated_positions.value_in_unit_system(unit.md_unit_system)]), unit=unit.nanometers)
        atp.topology = solvated_topology

        atp.system = system_generator.create_system(atp.topology)


    return atp, system_generator

def generate_dipeptide_top_pos_sys(topology,
                                   new_res,
                                   system,
                                   positions,
                                   system_generator,
                                   extra_sidechain_map=None,
                                   demap_CBs=False,
                                   conduct_geometry_prop=True,
                                   conduct_htf_prop = False,
                                   validate_energy_bookkeeping=True,
                                   generate_repartitioned_hybrid_topology_factory=False,
                                   generate_rest_capable_hybrid_topology_factory=False,
                                   endstate=None,
                                   flatten_torsions=False,
                                   flatten_exceptions=False,
                                   validate_endstate_energy=True # Cannot validate endstate energies if flatten_torsions/exceptions is True
                                   ):
    """generate point mutation engine, geometry_engine, and conduct topology proposal, geometry propsal, and hybrid factory generation"""
    from perses.tests.utils import validate_endstate_energies
    import copy

    if conduct_htf_prop:
        assert conduct_geometry_prop, f"the htf prop can only be conducted if there is a geometry proposal"

    # Create the point mutation engine
    from perses.rjmc.topology_proposal import PointMutationEngine
    point_mutation_engine = PointMutationEngine(wildtype_topology=topology,
                                                system_generator=system_generator,
                                                chain_id='1', # Denote the chain id allowed to mutate (it's always a string variable)
                                                max_point_mutants=1,
                                                residues_allowed_to_mutate=['2'], # The residue ids allowed to mutate
                                                allowed_mutations=[('2', new_res)], # The residue ids allowed to mutate with the three-letter code allowed to change
                                                aggregate=True) # Always allow aggregation

    # Create a top proposal
    print(f"making topology proposal")
    topology_proposal = point_mutation_engine.propose(current_system=system, current_topology=topology, extra_sidechain_map=extra_sidechain_map, demap_CBs=demap_CBs)

    if not conduct_geometry_prop:
        return topology_proposal

    if conduct_geometry_prop:
        # Create a geometry engine
        print(f"generating geometry engine")
        from perses.rjmc.geometry import FFAllAngleGeometryEngine
        geometry_engine = FFAllAngleGeometryEngine(metadata=None,
                                               use_sterics=False,
                                               n_bond_divisions=100,
                                               n_angle_divisions=180,
                                               n_torsion_divisions=360,
                                               verbose=True,
                                               storage=None,
                                               bond_softening_constant=1.0,
                                               angle_softening_constant=1.0,
                                               neglect_angles = False,
                                               use_14_nonbondeds = True)


        # Make a geometry proposal forward
        print(f"making geometry proposal from {list(topology.residues())[1].name} to {new_res}")
        forward_new_positions, logp_proposal = geometry_engine.propose(topology_proposal, positions, beta, validate_energy_bookkeeping=validate_energy_bookkeeping)
        logp_reverse = geometry_engine.logp_reverse(topology_proposal, forward_new_positions, positions, beta, validate_energy_bookkeeping=validate_energy_bookkeeping)

    if not conduct_htf_prop:
        return (topology_proposal, forward_new_positions, logp_proposal, logp_reverse)

    if conduct_htf_prop:
        # Create a hybrid topology factory
        if generate_repartitioned_hybrid_topology_factory:
            from perses.annihilation.relative import RepartitionedHybridTopologyFactory
            factory = RepartitionedHybridTopologyFactory
            assert endstate in [0, 1], "endstate must be 0 or 1"
        elif generate_rest_capable_hybrid_topology_factory:
            from perses.annihilation.relative import RESTCapableHybridTopologyFactory
            factory = RESTCapableHybridTopologyFactory
        else:
            from perses.annihilation.relative import HybridTopologyFactory
            factory = HybridTopologyFactory

        forward_htf = factory(topology_proposal=topology_proposal,
                     current_positions=positions,
                     new_positions=forward_new_positions,
                     use_dispersion_correction=False,
                     functions=None,
                     softcore_alpha=None,
                     bond_softening_constant=1.0,
                     angle_softening_constant=1.0,
                     soften_only_new=False,
                     neglected_new_angle_terms=[],
                     neglected_old_angle_terms=[],
                     softcore_LJ_v2=True,
                     softcore_electrostatics=True,
                     softcore_LJ_v2_alpha=0.85,
                     softcore_electrostatics_alpha=0.3,
                     softcore_sigma_Q=1.0,
                     interpolate_old_and_new_14s=flatten_exceptions,
                     omitted_terms=None,
                     endstate=endstate,
                     flatten_torsions=flatten_torsions)

        if not validate_endstate_energy:
            return forward_htf
        else:
            assert not flatten_torsions and not flatten_exceptions, "Cannot conduct endstate validation if flatten_torsions or flatten_exceptions is True"

            if generate_rest_capable_hybrid_topology_factory:
                from perses.tests.utils import validate_endstate_energies_point
                for endstate in [0, 1]:
                    htf = copy.deepcopy(forward_htf)
                    validate_endstate_energies_point(htf, endstate=endstate, minimize=True)
            else:
                from perses.tests.utils import validate_endstate_energies

                if not topology_proposal.unique_new_atoms:
                    assert geometry_engine.forward_final_context_reduced_potential == None, f"There are no unique new atoms but the geometry_engine's final context reduced potential is not None (i.e. {geometry_engine.forward_final_context_reduced_potential})"
                    assert geometry_engine.forward_atoms_with_positions_reduced_potential == None, f"There are no unique new atoms but the geometry_engine's forward atoms-with-positions-reduced-potential in not None (i.e. { geometry_engine.forward_atoms_with_positions_reduced_potential})"
                    vacuum_added_valence_energy = 0.0
                else:
                    added_valence_energy = geometry_engine.forward_final_context_reduced_potential - geometry_engine.forward_atoms_with_positions_reduced_potential

                if not topology_proposal.unique_old_atoms:
                    assert geometry_engine.reverse_final_context_reduced_potential == None, f"There are no unique old atoms but the geometry_engine's final context reduced potential is not None (i.e. {geometry_engine.reverse_final_context_reduced_potential})"
                    assert geometry_engine.reverse_atoms_with_positions_reduced_potential == None, f"There are no unique old atoms but the geometry_engine's atoms-with-positions-reduced-potential in not None (i.e. { geometry_engine.reverse_atoms_with_positions_reduced_potential})"
                    subtracted_valence_energy = 0.0
                else:
                    subtracted_valence_energy = geometry_engine.reverse_final_context_reduced_potential - geometry_engine.reverse_atoms_with_positions_reduced_potential

                if generate_repartitioned_hybrid_topology_factory:

                    if endstate == 0:
                        zero_state_error, _ = validate_endstate_energies(forward_htf._topology_proposal,
                                                                         forward_htf,
                                                                         added_valence_energy,
                                                                         subtracted_valence_energy,
                                                                         beta=beta,
                                                                         ENERGY_THRESHOLD=ENERGY_THRESHOLD,
                                                                         platform=openmm.Platform.getPlatformByName('Reference'),
                                                                         repartitioned_endstate=endstate)
                    else:
                        _, one_state_error = validate_endstate_energies(forward_htf._topology_proposal,
                                                                        forward_htf,
                                                                        added_valence_energy,
                                                                        subtracted_valence_energy,
                                                                        beta=beta,
                                                                        ENERGY_THRESHOLD=ENERGY_THRESHOLD,
                                                                        platform=openmm.Platform.getPlatformByName('Reference'),
                                                                        repartitioned_endstate=endstate)

                else:
                    zero_state_error, one_state_error = validate_endstate_energies(forward_htf._topology_proposal,
                                                                                   forward_htf,
                                                                                   added_valence_energy,
                                                                                   subtracted_valence_energy,
                                                                                   beta=beta,
                                                                                   ENERGY_THRESHOLD=ENERGY_THRESHOLD,
                                                                                   platform=openmm.Platform.getPlatformByName('Reference'))

            return forward_htf


def test_mutate_from_alanine():
    """
    generate alanine dipeptide system (vacuum) and mutating to every other amino acid as a sanity check...
    """
    # TODO: run the full pipeline for all of the aminos; at the moment, large perturbations (i.e. to ARG have the potential of
    #      generating VERY large nonbonded energies, to which numerical precision cannot achieve a proper threshold of 1e-6.
    #      in the future, we can look to use sterics or something fancy.  At the moment, we recommend conservative transforms
    #      or transforms that have more unique _old_ atoms than new
    aminos = ['ARG', 'ASH', 'ASN', 'ASP', 'CYS', 'GLH', 'GLN', 'GLU', 'GLY', 'HID', 'HIE', 'HIS', 'HIP', 'ILE', 'LEU', 'LYN', 'LYS', 'MET', 'PHE', 'SER', 'THR', 'TRP', 'TYR', 'VAL']
    attempt_full_pipeline_aminos = ['CYS', 'ILE', 'SER', 'THR', 'VAL'] #let's omit rings and large perturbations for now

    ala, system_generator = generate_atp()

    for amino in aminos:
        if amino in attempt_full_pipeline_aminos:
            _ = generate_dipeptide_top_pos_sys(ala.topology, amino, ala.system, ala.positions, system_generator, conduct_htf_prop=True)
        else:
            _ = generate_dipeptide_top_pos_sys(ala.topology, amino, ala.system, ala.positions, system_generator, conduct_geometry_prop=False)

def test_protein_atom_maps():

    # Get alanine dipeptide in vacuum test system
    ala, system_generator = generate_atp()


    # Define function for checking that the atom map is correct
    def check_atom_map(topology_proposal, reference_map):
        # Retrieve atom index to name mapping for old and new residues
        old_res = [res for res in topology_proposal.old_topology.residues() if res.name == topology_proposal.old_residue_name][0]
        new_res = [res for res in topology_proposal.new_topology.residues() if res.name == topology_proposal.new_residue_name][0]
        old_res_index_to_name = {atom.index: atom.name for atom in old_res.atoms()}
        new_res_index_to_name = {atom.index: atom.name for atom in new_res.atoms()}

        # Check whether the atom map generated matches the reference map
        atom_map = topology_proposal._core_new_to_old_atom_map

        mapped_atoms = [(new_res_index_to_name[new_idx], old_res_index_to_name[old_idx]) for new_idx, old_idx in atom_map.items() if new_idx in new_res_index_to_name.keys() and old_idx in old_res_index_to_name.keys()]
        assert sorted(reference_map) == sorted(mapped_atoms), f"{topology_proposal.old_residue_name}->{topology_proposal.new_residue_name} map does not match reference map"

    # ALA -> SER
    topology_proposal, new_positions, logp_proposal, logp_reverse = generate_dipeptide_top_pos_sys(ala.topology, 'SER', ala.system, ala.positions, system_generator, conduct_geometry_prop=True)
    ser_topology, ser_system = topology_proposal.new_topology, topology_proposal.new_system
    reference_map = [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('HA', 'HA'), ('C', 'C'), ('O', 'O'), ('CB', 'CB')]
    check_atom_map(topology_proposal, reference_map)

    # SER -> ALA
    topology_proposal = generate_dipeptide_top_pos_sys(ser_topology, 'ALA', ser_system, new_positions, system_generator, conduct_geometry_prop=False)
    reference_map = [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('HA', 'HA'), ('C', 'C'), ('O', 'O'), ('CB', 'CB')]
    check_atom_map(topology_proposal, reference_map)

    # ALA -> VAL
    topology_proposal, new_positions, logp_proposal, logp_reverse = generate_dipeptide_top_pos_sys(ala.topology, 'VAL', ala.system, ala.positions, system_generator, conduct_geometry_prop=True)
    val_topology, val_system = topology_proposal.new_topology, topology_proposal.new_system
    reference_map =  [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('HA', 'HA'), ('C', 'C'), ('O', 'O'), ('CB', 'CB')]
    check_atom_map(topology_proposal, reference_map)

    # VAL -> ALA
    topology_proposal = generate_dipeptide_top_pos_sys(val_topology, 'ALA', val_system, new_positions, system_generator, conduct_geometry_prop=False)
    reference_map = [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('HA', 'HA'), ('C', 'C'), ('O', 'O'), ('CB', 'CB')]
    check_atom_map(topology_proposal, reference_map)

    # VAL -> ILE
    topology_proposal = generate_dipeptide_top_pos_sys(val_topology, 'ILE', val_system, new_positions, system_generator, conduct_geometry_prop=False)
    reference_map = [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('HA', 'HA'), ('C', 'C'), ('O', 'O'), ('CB', 'CB')]
    check_atom_map(topology_proposal, reference_map)

    # VAL -> ILE with extra sidechain map
    topology_proposal = generate_dipeptide_top_pos_sys(val_topology, 'ILE', val_system, new_positions, system_generator, extra_sidechain_map={13:13, 17: 18, 18: 19, 19: 20, 20: 21}, conduct_geometry_prop=False)
    reference_map = [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('HA', 'HA'), ('C', 'C'), ('O', 'O'), ('CB', 'CB'), ('HB', 'HB'), ('CG2', 'CG2'), ('HG21', 'HG21'), ('HG22', 'HG22'), ('HG23', 'HG23')]
    check_atom_map(topology_proposal, reference_map)

    # ALA -> GLY
    topology_proposal, new_positions, logp_proposal, logp_reverse = generate_dipeptide_top_pos_sys(ala.topology, 'GLY', ala.system, ala.positions, system_generator, conduct_geometry_prop=True)
    gly_topology, gly_system = topology_proposal.new_topology, topology_proposal.new_system
    reference_map = [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('C', 'C'), ('O', 'O')]
    check_atom_map(topology_proposal, reference_map)

    # GLY -> ALA
    topology_proposal = generate_dipeptide_top_pos_sys(gly_topology, 'ALA', gly_system, new_positions, system_generator, conduct_geometry_prop=False)
    reference_map = [('N', 'N'), ('H', 'H'), ('CA', 'CA'), ('C', 'C'), ('O', 'O')]
    check_atom_map(topology_proposal, reference_map)

#@attr('advanced')
def test_specify_allowed_mutants():
    """
    Make sure proposals can be made using optional argument allowed_mutations

    This test has three possible insulin systems: wild type, Q5E, and Q5N/Y14F
    """
    chain_id = 'A'
    allowed_mutations = [('5','GLU'),('5','ASN'),('14','PHE')]
    import perses.rjmc.topology_proposal as topology_proposal

    pdbid = "2HIU"
    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)
    for chain in modeller.topology.chains():
        pass

    modeller.delete([chain])

    system_generator = create_simple_protein_system_generator()

    system = system_generator.create_system(modeller.topology)
    chain_id = 'A'

    for chain in modeller.topology.chains():
        if chain.id == chain_id:
            residues = chain._residues
    mutant_res = np.random.choice(residues[1:-1])

    pm_top_engine = topology_proposal.PointMutationEngine(modeller.topology, system_generator, chain_id, allowed_mutations=allowed_mutations)


    ntrials = 10
    for trian in range(ntrials):
        pm_top_proposal = pm_top_engine.propose(system, modeller.topology)
        # Check to make sure no out-of-bounds atoms are present in new_to_old_atom_map
        natoms_old = pm_top_proposal.n_atoms_old
        natoms_new = pm_top_proposal.n_atoms_new
        if not set(pm_top_proposal.new_to_old_atom_map.values()).issubset(range(natoms_old)):
            msg = "Some old atoms in TopologyProposal.new_to_old_atom_map are not in span of old atoms (1..%d):\n" % natoms_old
            msg += str(pm_top_proposal.new_to_old_atom_map)
            raise Exception(msg)
        if not set(pm_top_proposal.new_to_old_atom_map.keys()).issubset(range(natoms_new)):
            msg = "Some new atoms in TopologyProposal.new_to_old_atom_map are not in span of old atoms (1..%d):\n" % natoms_new
            msg += str(pm_top_proposal.new_to_old_atom_map)
            raise Exception(msg)

#@attr('advanced')
def test_propose_self():
    """
    Propose a mutation to remain at WT in insulin
    """
    import perses.rjmc.topology_proposal as topology_proposal

    pdbid = "2HIU"
    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)
    for chain in modeller.topology.chains():
        pass

    modeller.delete([chain])

    system_generator = create_simple_protein_system_generator()

    system = system_generator.create_system(modeller.topology)
    chain_id = 'A'

    for chain in modeller.topology.chains():
        if chain.id == chain_id:
            residues = [res for res in chain._residues if res.name not in PROHIBITED_RESIDUES]
    mutant_res = np.random.choice(residues[1:-1])
    allowed_mutations = [(mutant_res.id,mutant_res.name)]

    pm_top_engine = topology_proposal.PointMutationEngine(modeller.topology, system_generator, chain_id, allowed_mutations=allowed_mutations)
    pm_top_proposal = pm_top_engine.propose(system, modeller.topology)
    assert pm_top_proposal.old_topology == pm_top_proposal.new_topology
    assert pm_top_proposal.old_system == pm_top_proposal.new_system
    assert pm_top_proposal.old_chemical_state_key == pm_top_proposal.new_chemical_state_key

#@attr('advanced')
def test_run_point_mutation_propose():
    """
    Propose a random mutation in insulin
    """
    import perses.rjmc.topology_proposal as topology_proposal

    pdbid = "2HIU"
    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)
    for chain in modeller.topology.chains():
        pass

    modeller.delete([chain])

    max_point_mutants = 1
    chain_id = 'A'

    # Pull the allowable mutatable residues..
    _chain = [chain for chain in modeller.topology.chains() if chain.id == chain_id][0]
    residue_ids = [residue.id for residue in _chain.residues() if residue.name != 'CYS'][1:-1]

    system_generator = create_simple_protein_system_generator()
    system = system_generator.create_system(modeller.topology)

    pm_top_engine = topology_proposal.PointMutationEngine(modeller.topology, system_generator, chain_id, max_point_mutants=max_point_mutants, residues_allowed_to_mutate=residue_ids)
    pm_top_proposal = pm_top_engine.propose(system, modeller.topology)

#@attr('advanced')
def test_alanine_dipeptide_map():
    pdb_filename = resource_filename('openmmtools', 'data/alanine-dipeptide-gbsa/alanine-dipeptide.pdb')
    from simtk.openmm.app import PDBFile
    pdbfile = PDBFile(pdb_filename)
    import perses.rjmc.topology_proposal as topology_proposal
    modeller = app.Modeller(pdbfile.topology, pdbfile.positions)

    allowed_mutations = [('2', 'PHE')]
    system_generator = create_simple_protein_system_generator()
    system = system_generator.create_system(modeller.topology)
    chain_id = ' '

    metadata = dict()
    pm_top_engine = topology_proposal.PointMutationEngine(modeller.topology, system_generator, chain_id, proposal_metadata=metadata, allowed_mutations=allowed_mutations, always_change=True)

    proposal = pm_top_engine.propose(system, modeller.topology)

    new_topology = proposal.new_topology
    new_system = proposal.new_system
    old_topology = proposal.old_topology
    old_system = proposal.old_system
    atom_map = proposal.old_to_new_atom_map

    for k, atom in enumerate(old_topology.atoms()):
        atom_idx = atom.index
        if atom_idx in atom_map.keys():
            atom2_idx = atom_map[atom_idx]
            for l, atom2 in enumerate(new_topology.atoms()):
                if atom2.index == atom2_idx:
                    new_atom = atom2
                    break
            old_name = atom.name
            new_name = new_atom.name
            print('\n%s to %s' % (str(atom.residue), str(new_atom.residue)))
            print('old_atom.index vs index in topology: %s %s' % (atom_idx, k))
            print('new_atom.index vs index in topology: %s %s' % (atom2_idx, l))
            print('Who was matched: old %s to new %s' % (old_name, new_name))
            if atom2_idx != l:
                mass_by_map = system.getParticleMass(atom2_idx)
                mass_by_sys = system.getParticleMass(l)
                print('Should have matched %s actually got %s' % (mass_by_map, mass_by_sys))
                raise Exception(f"there is an atom mismatch")

@attr('advanced')
def test_mutate_from_every_amino_to_every_other():
    """
    Make sure mutations are successful between every possible pair of before-and-after residues
    Mutate Ecoli F-ATPase alpha subunit to all 20 amino acids (test going FROM all possibilities)
    Mutate each residue to all 19 alternatives
    """
    import perses.rjmc.topology_proposal as topology_proposal

    aminos = ['ALA','ARG','ASN','ASP','CYS','GLN','GLU','GLY','HIS','ILE','LEU','LYS','MET','PHE','PRO','SER','THR','TRP','TYR','VAL']

    failed_mutants = 0

    pdbid = "2A7U"
    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)
    for chain in modeller.topology.chains():
        pass

    modeller.delete([chain])

    ff_filename = "amber99sbildn.xml"
    max_point_mutants = 1

    ff = app.ForceField(ff_filename)
    system = ff.createSystem(modeller.topology)
    chain_id = 'A'

    metadata = dict()

    system_generator = SystemGenerator([ff_filename])

    pm_top_engine = topology_proposal.PointMutationEngine(modeller.topology, system_generator, chain_id, proposal_metadata=metadata, max_point_mutants=max_point_mutants, always_change=True)

    current_system = system
    current_topology = modeller.topology
    current_positions = modeller.positions

    pm_top_engine._allowed_mutations = list()
    for k, proposed_amino in enumerate(aminos):
        pm_top_engine._allowed_mutations.append((str(k+2),proposed_amino))
    pm_top_proposal = pm_top_engine.propose(current_system, current_topology)
    current_system = pm_top_proposal.new_system
    current_topology = pm_top_proposal.new_topology

    for chain in current_topology.chains():
        if chain.id == chain_id:
            # num_residues : int
            num_residues = len(chain._residues)
            break
    new_sequence = list()
    for residue in current_topology.residues():
        if residue.index == 0:
            continue
        if residue.index == (num_residues -1):
            continue
        new_sequence.append(residue.name)
    for i in range(len(aminos)):
        assert new_sequence[i] == aminos[i]


    pm_top_engine = topology_proposal.PointMutationEngine(current_topology, system_generator, chain_id, proposal_metadata=metadata, max_point_mutants=max_point_mutants)

    from perses.rjmc.topology_proposal import append_topology
    old_topology = app.Topology()
    append_topology(old_topology, current_topology)
    new_topology = app.Topology()
    append_topology(new_topology, current_topology)

    old_chemical_state_key = pm_top_engine.compute_state_key(old_topology)


    for chain in new_topology.chains():
        if chain.id == chain_id:
            # num_residues : int
            num_residues = len(chain._residues)
            break
    for proposed_location in range(1, num_residues-1):
        print('Making mutations at residue %s' % proposed_location)
        original_residue_name = chain._residues[proposed_location].name
        matching_amino_found = 0
        for proposed_amino in aminos:
            pm_top_engine._allowed_mutations = [(str(proposed_location+1),proposed_amino)]
            new_topology = app.Topology()
            append_topology(new_topology, current_topology)
            old_system = current_system
            old_topology_natoms = sum([1 for atom in old_topology.atoms()])
            old_system_natoms = old_system.getNumParticles()
            if old_topology_natoms != old_system_natoms:
                msg = 'PolymerProposalEngine: old_topology has %d atoms, while old_system has %d atoms' % (old_topology_natoms, old_system_natoms)
                raise Exception(msg)
            metadata = dict()

            for atom in new_topology.atoms():
                atom.old_index = atom.index

            index_to_new_residues, metadata = pm_top_engine._choose_mutant(new_topology, metadata)
            if len(index_to_new_residues) == 0:
                matching_amino_found+=1
                continue
            print('Mutating %s to %s' % (original_residue_name, proposed_amino))

            residue_map = pm_top_engine._generate_residue_map(new_topology, index_to_new_residues)
            for res_pair in residue_map:
                residue = res_pair[0]
                name = res_pair[1]
                assert residue.index in index_to_new_residues.keys()
                assert index_to_new_residues[residue.index] == name
                assert residue.name+'-'+str(residue.id)+'-'+name in metadata['mutations']

            new_topology, missing_atoms = pm_top_engine._delete_excess_atoms(new_topology, residue_map)
            new_topology = pm_top_engine._add_new_atoms(new_topology, missing_atoms, residue_map)
            for res_pair in residue_map:
                residue = res_pair[0]
                name = res_pair[1]
                assert residue.name == name

            atom_map = pm_top_engine._construct_atom_map(residue_map, old_topology, index_to_new_residues, new_topology)
            templates = pm_top_engine._ff.getMatchingTemplates(new_topology)
            assert [templates[index].name == residue.name for index, (residue, name) in enumerate(residue_map)]

            new_chemical_state_key = pm_top_engine.compute_state_key(new_topology)
            new_system = pm_top_engine._system_generator.build_system(new_topology)
            pm_top_proposal = topology_proposal.TopologyProposal(new_topology=new_topology,
                                                                 new_system=new_system,
                                                                 old_topology=old_topology,
                                                                 old_system=old_system,
                                                                 old_chemical_state_key=old_chemical_state_key,
                                                                 new_chemical_state_key=new_chemical_state_key,
                                                                 logp_proposal=0.0,
                                                                 new_to_old_atom_map=atom_map)

        assert matching_amino_found == 1

@attr('advanced')
def test_limiting_allowed_residues():
    """
    Test example system with certain mutations allowed to mutate
    """
    import perses.rjmc.topology_proposal as topology_proposal

    failed_mutants = 0

    pdbid = "1G3F"
    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)

    chain_id = 'B'
    to_delete = list()
    for chain in modeller.topology.chains():
        if chain.id != chain_id:
            to_delete.append(chain)
    modeller.delete(to_delete)
    modeller.addHydrogens()

    ff_filename = "amber99sbildn.xml"

    ff = app.ForceField(ff_filename)
    system = ff.createSystem(modeller.topology)

    system_generator = SystemGenerator([ff_filename])

    max_point_mutants = 1
    residues_allowed_to_mutate = ['903','904','905']

    pl_top_library = topology_proposal.PointMutationEngine(modeller.topology,
                                                           system_generator,
                                                           chain_id,
                                                           max_point_mutants=max_point_mutants,
                                                           residues_allowed_to_mutate=residues_allowed_to_mutate)
    pl_top_proposal = pl_top_library.propose(system, modeller.topology)

@attr('advanced')
def test_always_change():
    """
    Test 'always_change' argument in topology proposal
    Allowing one residue to mutate, must change to a different residue each
    of 50 iterations
    """
    import perses.rjmc.topology_proposal as topology_proposal

    pdbid = "1G3F"
    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)

    chain_id = 'B'
    to_delete = list()
    for chain in modeller.topology.chains():
        if chain.id != chain_id:
            to_delete.append(chain)
    modeller.delete(to_delete)
    modeller.addHydrogens()

    ff_filename = "amber99sbildn.xml"

    ff = app.ForceField(ff_filename)
    system = ff.createSystem(modeller.topology)

    system_generator = SystemGenerator([ff_filename])

    max_point_mutants = 1
    residues_allowed_to_mutate = ['903']

    for residue in modeller.topology.residues():
        if residue.id in residues_allowed_to_mutate:
            print('Old residue: %s' % residue.name)
            old_res_name = residue.name
    pl_top_library = topology_proposal.PointMutationEngine(modeller.topology,
                                                           system_generator,
                                                           chain_id,
                                                           max_point_mutants=max_point_mutants,
                                                           residues_allowed_to_mutate=residues_allowed_to_mutate,
                                                           always_change=True)
    topology = modeller.topology
    for i in range(50):
        pl_top_proposal = pl_top_library.propose(system, topology)
        for residue in pl_top_proposal.new_topology.residues():
            if residue.id in residues_allowed_to_mutate:
                print('Iter %s New residue: %s' % (i, residue.name))
                new_res_name = residue.name
        assert(old_res_name != new_res_name)
        old_res_name = new_res_name
        topology = pl_top_proposal.new_topology
        system = pl_top_proposal.new_system

@attr('advanced')
def test_run_peptide_library_engine():
    """
    Test example system with peptide and library
    """
    import perses.rjmc.topology_proposal as topology_proposal

    failed_mutants = 0

    pdbid = "1G3F"
    topology, positions = load_pdbid_to_openmm(pdbid)
    modeller = app.Modeller(topology, positions)

    chain_id = 'B'
    to_delete = list()
    for chain in modeller.topology.chains():
        if chain.id != chain_id:
            to_delete.append(chain)
    modeller.delete(to_delete)
    modeller.addHydrogens()

    ff_filename = "amber99sbildn.xml"

    ff = app.ForceField(ff_filename)
    ff.loadFile("tip3p.xml")
    modeller.addSolvent(ff)
    system = ff.createSystem(modeller.topology)

    system_generator = SystemGenerator([ff_filename])
    library = ['AVILMFYQP','RHKDESTNQ','STNQCFGPL']

    pl_top_library = topology_proposal.PeptideLibraryEngine(system_generator, library, chain_id)

    pl_top_proposal = pl_top_library.propose(system, modeller.topology)

def test_protein_counterion_topology_fix_positive():
    """
    mutate alanine dipeptide into ASP dipeptide and assert that the appropriate number of water indices are identified
    """
    from perses.rjmc.topology_proposal import PolymerProposalEngine
    new_res = 'ASP'
    charge_diff = 1

    # Make a vacuum system
    atp, system_generator = generate_atp(phase='vacuum')

    # Make a solvated system/topology/positions with modeller
    modeller = app.Modeller(atp.topology, atp.positions)
    modeller.addSolvent(system_generator.forcefield, model='tip3p', padding=11*unit.angstroms, ionicStrength=0.15*unit.molar)
    solvated_topology = modeller.getTopology()
    solvated_positions = modeller.getPositions()

    # Canonicalize the solvated positions: turn tuples into np.array
    atp.positions = unit.quantity.Quantity(value=np.array([list(atom_pos) for atom_pos in solvated_positions.value_in_unit_system(unit.md_unit_system)]), unit=unit.nanometers)
    atp.topology = solvated_topology

    atp.system = system_generator.create_system(atp.topology)

    # Make a topology proposal and generate new positions
    top_proposal, new_pos, _, _ = generate_dipeptide_top_pos_sys(topology = atp.topology,
                                   new_res = new_res,
                                   system = atp.system,
                                   positions = atp.positions,
                                   system_generator = system_generator,
                                   conduct_geometry_prop = True,
                                   conduct_htf_prop = False,
                                   validate_energy_bookkeeping=True,
                                   )

    # Get the charge difference
    charge_diff_test = PolymerProposalEngine._get_charge_difference(top_proposal._old_topology.residue_topology.name,
                                                                top_proposal._new_topology.residue_topology.name)
    assert charge_diff_test == charge_diff

    # Get the array of water indices (w.r.t. new topology) to turn into ions
    water_indices = get_water_indices(charge_diff=charge_diff_test,
                                      new_positions=new_pos,
                                      new_topology=top_proposal._new_topology,
                                      radius=0.8)

    assert len(water_indices) == 3

def test_protein_counterion_topology_fix_negitive():
    """
    mutate alanine dipeptide into ARG dipeptide and assert that the appropriate number of water indices are identified
    """
    from perses.rjmc.topology_proposal import PolymerProposalEngine
    new_res = 'ARG'
    charge_diff = -1

    # Make a vacuum system
    atp, system_generator = generate_atp(phase='vacuum')

    # Make a solvated system/topology/positions with modeller
    modeller = app.Modeller(atp.topology, atp.positions)
    modeller.addSolvent(system_generator.forcefield, model='tip3p', padding=11*unit.angstroms, ionicStrength=0.15*unit.molar)
    solvated_topology = modeller.getTopology()
    solvated_positions = modeller.getPositions()

    # Canonicalize the solvated positions: turn tuples into np.array
    atp.positions = unit.quantity.Quantity(value=np.array([list(atom_pos) for atom_pos in solvated_positions.value_in_unit_system(unit.md_unit_system)]), unit=unit.nanometers)
    atp.topology = solvated_topology

    atp.system = system_generator.create_system(atp.topology)

    # Make a topology proposal and generate new positions
    top_proposal, new_pos, _, _ = generate_dipeptide_top_pos_sys(topology = atp.topology,
                                   new_res = new_res,
                                   system = atp.system,
                                   positions = atp.positions,
                                   system_generator = system_generator,
                                   conduct_geometry_prop = True,
                                   conduct_htf_prop = False,
                                   validate_energy_bookkeeping=True,
                                   )

    # Get the charge difference
    charge_diff_test = PolymerProposalEngine._get_charge_difference(top_proposal._old_topology.residue_topology.name,
                                                                top_proposal._new_topology.residue_topology.name)
    assert charge_diff_test == charge_diff

    # Get the array of water indices (w.r.t. new topology) to turn into ions
    water_indices = get_water_indices(charge_diff=charge_diff_test,
                                      new_positions=new_pos,
                                      new_topology=top_proposal._new_topology,
                                      radius=0.8)

    assert len(water_indices) == 3


def test_protein_counterion_topology_fix_zero():
    """
    mutate alanine dipeptide into ASN dipeptide and assert that the appropriate number of water indices are identified
    """
    from perses.rjmc.topology_proposal import PolymerProposalEngine
    new_res = 'ASN'
    charge_diff = 0

    # Make a vacuum system
    atp, system_generator = generate_atp(phase='vacuum')

    # Make a solvated system/topology/positions with modeller
    modeller = app.Modeller(atp.topology, atp.positions)
    modeller.addSolvent(system_generator.forcefield, model='tip3p', padding=11*unit.angstroms, ionicStrength=0.15*unit.molar)
    solvated_topology = modeller.getTopology()
    solvated_positions = modeller.getPositions()

    # Canonicalize the solvated positions: turn tuples into np.array
    atp.positions = unit.quantity.Quantity(value=np.array([list(atom_pos) for atom_pos in solvated_positions.value_in_unit_system(unit.md_unit_system)]), unit=unit.nanometers)
    atp.topology = solvated_topology

    atp.system = system_generator.create_system(atp.topology)

    # Make a topology proposal and generate new positions
    top_proposal, new_pos, _, _ = generate_dipeptide_top_pos_sys(topology = atp.topology,
                                   new_res = new_res,
                                   system = atp.system,
                                   positions = atp.positions,
                                   system_generator = system_generator,
                                   conduct_geometry_prop = True,
                                   conduct_htf_prop = False,
                                   validate_energy_bookkeeping=True,
                                   )

    # Get the charge difference
    charge_diff_test = PolymerProposalEngine._get_charge_difference(top_proposal._old_topology.residue_topology.name,
                                                                top_proposal._new_topology.residue_topology.name)
    assert charge_diff_test == charge_diff

    # Get the array of water indices (w.r.t. new topology) to turn into ions
    water_indices = get_water_indices(charge_diff=charge_diff_test,
                                      new_positions=new_pos,
                                      new_topology=top_proposal._new_topology,
                                      radius=0.8)

    assert len(water_indices) == 0
