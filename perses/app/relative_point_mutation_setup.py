from __future__ import absolute_import

from perses.utils.openeye import createOEMolFromSDF, extractPositionsFromOEMol
from perses.annihilation.relative import HybridTopologyFactory, RepartitionedHybridTopologyFactory
from perses.rjmc.topology_proposal import PointMutationEngine
from perses.rjmc.geometry import FFAllAngleGeometryEngine

import simtk.openmm as openmm
import simtk.openmm.app as app
import simtk.unit as unit
import numpy as np
from openmoltools import forcefield_generators
import mdtraj as md
from openmmtools.constants import kB
from perses.tests.utils import validate_endstate_energies
from openff.toolkit.topology import Molecule
from openmmforcefields.generators import SystemGenerator

ENERGY_THRESHOLD = 1e-2
temperature = 300 * unit.kelvin
kT = kB * temperature
beta = 1.0/kT
ring_amino_acids = ['TYR', 'PHE', 'TRP', 'PRO', 'HIS']

# Set up logger
import logging
_logger = logging.getLogger()
_logger.setLevel(logging.INFO)

class PointMutationExecutor(object):
    """
    Simple, stripped-down class to create a protein-ligand system and allow a mutation of a protein.
    this will allow support for the creation of _two_ relative free energy calculations:
        1. 'wildtype' - 'point mutant' complex hybrid.
        2. 'wildtype' - 'point mutant' protein hybrid (i.e. with ligand of interest unbound)

    Example (create full point mutation executor and run parallel tempering on both complex and apo phases):
        from pkg_resources import resource_filename

        protein_path = 'data/perses_jacs_systems/thrombin/Thrombin_protein.pdb'
        ligands_path = 'data/perses_jacs_systems/thrombin/Thrombin_ligands.sdf'
        protein_filename = resource_filename('openmmforcefields', protein_path)
        ligand_input = resource_filename('openmmforcefields', ligands_path)

        pm_delivery = PointMutationExecutor(protein_filename=protein_filename,
                                    mutation_chain_id='2',
                                    mutation_residue_id='198',
                                     proposed_residue='THR',
                                     phase='complex',
                                     conduct_endstate_validation=False,
                                     ligand_input=ligand_input,
                                     ligand_index=0,
                                     forcefield_files=['amber14/protein.ff14SB.xml', 'amber14/tip3p.xml'],
                                     barostat=openmm.MonteCarloBarostat(1.0 * unit.atmosphere, temperature, 50),
                                     forcefield_kwargs={'removeCMMotion': False, 'ewaldErrorTolerance': 1e-4, 'nonbondedMethod': app.PME, 'constraints' : app.HBonds, 'hydrogenMass' : 4 * unit.amus},
                                     small_molecule_forcefields='gaff-2.11')

        complex_htf = pm_delivery.get_complex_htf()
        apo_htf = pm_delivery.get_apo_htf()

        # Now we can build the hybrid repex samplers
        from perses.annihilation.lambda_protocol import LambdaProtocol
        from openmmtools.multistate import MultiStateReporter
        from perses.samplers.multistate import HybridRepexSampler
        from openmmtools import mcmc

        suffix = 'run'; selection = 'not water'; checkpoint_interval = 10; n_states = 11; n_cycles = 5000

        for htf in [complex_htf, apo_htf]:
            lambda_protocol = LambdaProtocol(functions='default')
            reporter_file = 'reporter.nc'
            reporter = MultiStateReporter(reporter_file, analysis_particle_indices = htf.hybrid_topology.select(selection), checkpoint_interval = checkpoint_interval)
            hss = HybridRepexSampler(mcmc_moves=mcmc.LangevinSplittingDynamicsMove(timestep= 4.0 * unit.femtoseconds,
                                                                                  collision_rate=5.0 / unit.picosecond,
                                                                                  n_steps=250,
                                                                                  reassign_velocities=False,
                                                                                  n_restart_attempts=20,
                                                                                  splitting="V R R R O R R R V",
                                                                                  constraint_tolerance=1e-06),
                                                                                  hybrid_factory=htf, online_analysis_interval=10)
            hss.setup(n_states=n_states, temperature=300*unit.kelvin, storage_file=reporter, lambda_protocol=lambda_protocol, endstates=False)
            hss.extend(n_cycles)

    """
    def __init__(self,
                 protein_filename,
                 mutation_chain_id,
                 mutation_residue_id,
                 proposed_residue,
                 phase='complex',
                 conduct_endstate_validation=True,
                 ligand_input=None,
                 ligand_index=0,
                 allow_undefined_stereo_sdf=False,
                 water_model='tip3p',
                 ionic_strength=0.15 * unit.molar,
                 forcefield_files=['amber14/protein.ff14SB.xml', 'amber14/tip3p.xml'],
                 barostat=openmm.MonteCarloBarostat(1.0 * unit.atmosphere, temperature, 50),
                 forcefield_kwargs={'removeCMMotion': False, 'ewaldErrorTolerance': 0.00025, 'constraints' : app.HBonds, 'hydrogenMass' : 4 * unit.amus},
                 periodic_forcefield_kwargs={'nonbondedMethod': app.PME},
                 nonperiodic_forcefield_kwargs=None,
                 small_molecule_forcefields='gaff-2.11',
                 complex_box_dimensions=None,
                 apo_box_dimensions=None,
                 flatten_torsions=False,
                 flatten_exceptions=False,
                 repartitioned_endstate=None,
                 **kwargs):
        """
        arguments
            protein_filename : str
                path to protein (to mutate); .pdb
            mutation_chain_id : str
                name of the chain to be mutated
            mutation_residue_id : str
                residue id to change
            proposed_residue : str
                three letter code of the residue to mutate to
            phase : str, default complex
                if phase == vacuum, then the complex will not be solvated with water; else, it will be solvated with tip3p
            conduct_endstate_validation : bool, default True
                whether to conduct an endstate validation of the HybridTopologyFactory. If using the RepartitionedHybridTopologyFactory,
                endstate validation cannot and will not be conducted.
            ligand_file : str, default None
                path to ligand of interest (i.e. small molecule or protein); .sdf or .pdb
            ligand_index : int, default 0
                which ligand to use
            allow_undefined_stereo_sdf : bool, default False
                whether to allow an SDF file to contain undefined stereocenters
            water_model : str, default 'tip3p'
                solvent model to use for solvation
            ionic_strength : float * unit.molar, default 0.15 * unit.molar
                the total concentration of ions (both positive and negative) to add using Modeller.
                This does not include ions that are added to neutralize the system.
                Note that only monovalent ions are currently supported.
            forcefield_files : list of str, default ['amber14/protein.ff14SB.xml', 'amber14/tip3p.xml']
                forcefield files for proteins and solvent
            barostat : openmm.MonteCarloBarostat, default openmm.MonteCarloBarostat(1.0 * unit.atmosphere, 300 * unit.kelvin, 50)
                barostat to use
            forcefield_kwargs : dict, default {'removeCMMotion': False, 'ewaldErrorTolerance': 1e-4, 'constraints' : app.HBonds, 'hydrogenMass' : 4 * unit.amus}
                forcefield kwargs for system parametrization
            periodic_forcefield_kwargs : dict, default {'nonbondedMethod': app.PME}
                periodic forcefield kwargs for system parametrization
            nonperiodic_forcefield_kwargs : dict, default None
                non-periodic forcefield kwargs for system parametrization
            small_molecule_forcefields : str, default 'gaff-2.11'
                the forcefield string for small molecule parametrization
            complex_box_dimensions : Vec3, default None
                define box dimensions of complex phase;
                if None, padding is 1nm
            apo_box_dimensions :  Vec3, default None
                define box dimensions of apo phase phase;
                if None, padding is 1nm
            flatten_torsions : bool, default False
                in the htf, flatten torsions involving unique new atoms at lambda = 0 and unique old atoms are lambda = 1
            flatten_exceptions : bool, default False
                in the htf, flatten exceptions involving unique new atoms at lambda = 0 and unique old atoms at lambda = 1
            repartitioned_endstate : int, default None
                the endstate (0 or 1) at which to build the RepartitionedHybridTopologyFactory. By default, this is None,
                meaning a vanilla HybridTopologyFactory will be built.
        TODO : allow argument for spectator ligands besides the 'ligand_file'

        """
        from openeye import oechem

        # First thing to do is load the apo protein to mutate...
        protein_pdbfile = open(protein_filename, 'r')
        protein_pdb = app.PDBFile(protein_pdbfile)
        protein_pdbfile.close()
        protein_positions, protein_topology, protein_md_topology = protein_pdb.positions, protein_pdb.topology, md.Topology.from_openmm(protein_pdb.topology)
        protein_topology = protein_md_topology.to_openmm()
        protein_n_atoms = protein_md_topology.n_atoms

        # Load the ligand, if present
        molecules = []
        if ligand_input:
            if isinstance(ligand_input, str):
                if ligand_input.endswith('.sdf'): # small molecule
                        ligand_mol = createOEMolFromSDF(ligand_file, index=ligand_index, allow_undefined_stereo=allow_undefined_stereo_sdf)
                        molecules.append(Molecule.from_openeye(ligand_mol, allow_undefined_stereo=False))
                        ligand_positions, ligand_topology = extractPositionsFromOEMol(ligand_mol),  forcefield_generators.generateTopologyFromOEMol(ligand_mol)
                        ligand_md_topology = md.Topology.from_openmm(ligand_topology)
                        ligand_n_atoms = ligand_md_topology.n_atoms

                if ligand_input.endswith('pdb'): # protein
                    ligand_pdbfile = open(ligand_input, 'r')
                    ligand_pdb = app.PDBFile(ligand_pdbfile)
                    ligand_pdbfile.close()
                    ligand_positions, ligand_topology, ligand_md_topology = ligand_pdb.positions, ligand_pdb.topology, md.Topology.from_openmm(
                        ligand_pdb.topology)
                    ligand_n_atoms = ligand_md_topology.n_atoms

            elif isinstance(ligand_input, oechem.OEMol): # oemol object
                molecules.append(Molecule.from_openeye(ligand_input, allow_undefined_stereo=False))
                ligand_positions, ligand_topology = extractPositionsFromOEMol(ligand_input),  forcefield_generators.generateTopologyFromOEMol(ligand_input)
                ligand_md_topology = md.Topology.from_openmm(ligand_topology)
                ligand_n_atoms = ligand_md_topology.n_atoms

            else:
                _logger.warning(f'ligand filetype not recognised. Please provide a path to a .pdb or .sdf file')
                return

            # Now create a complex
            complex_md_topology = protein_md_topology.join(ligand_md_topology)
            complex_topology = complex_md_topology.to_openmm()
            complex_positions = unit.Quantity(np.zeros([protein_n_atoms + ligand_n_atoms, 3]), unit=unit.nanometers)
            complex_positions[:protein_n_atoms, :] = protein_positions
            complex_positions[protein_n_atoms:, :] = ligand_positions

        # Now for a system_generator
        self.system_generator = SystemGenerator(forcefields=forcefield_files,
                                                barostat=barostat,
                                                forcefield_kwargs=forcefield_kwargs,
                                                periodic_forcefield_kwargs=periodic_forcefield_kwargs,
                                                nonperiodic_forcefield_kwargs=nonperiodic_forcefield_kwargs,
                                                small_molecule_forcefield=small_molecule_forcefields,
                                                molecules=molecules,
                                                cache=None)

        # Solvate apo and complex...
        apo_input = list(self._solvate(protein_topology, protein_positions, water_model, phase, ionic_strength, apo_box_dimensions))
        inputs = [apo_input]
        if ligand_input:
            inputs.append(self._solvate(complex_topology, complex_positions, water_model, phase, ionic_strength, complex_box_dimensions))

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


        # Run pipeline...
        htfs = []
        for (top, pos, sys) in inputs:
            point_mutation_engine = PointMutationEngine(wildtype_topology=top,
                                                                 system_generator=self.system_generator,
                                                                 chain_id=mutation_chain_id, # Denote the chain id allowed to mutate (it's always a string variable)
                                                                 max_point_mutants=1,
                                                                 residues_allowed_to_mutate=[mutation_residue_id], # The residue ids allowed to mutate
                                                                 allowed_mutations=[(mutation_residue_id, proposed_residue)], # The residue ids allowed to mutate with the three-letter code allowed to change
                                                                 aggregate=True) # Always allow aggregation

            topology_proposal = point_mutation_engine.propose(sys, top)

            # Only validate energy bookkeeping if the WT and proposed residues do not involve rings
            old_res = [res for res in top.residues() if res.id == mutation_residue_id][0]
            validate_bool = False if old_res.name in ring_amino_acids or proposed_residue in ring_amino_acids else True
            new_positions, logp_proposal = geometry_engine.propose(topology_proposal, pos, beta,
                                                                   validate_energy_bookkeeping=validate_bool)
            logp_reverse = geometry_engine.logp_reverse(topology_proposal, new_positions, pos, beta,
                                                        validate_energy_bookkeeping=validate_bool)

            if repartitioned_endstate is None:
                factory = HybridTopologyFactory
            elif repartitioned_endstate in [0, 1]:
                factory = RepartitionedHybridTopologyFactory

            forward_htf = factory(topology_proposal=topology_proposal,
                                  current_positions=pos,
                                  new_positions=new_positions,
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
                                  endstate=repartitioned_endstate,
                                  flatten_torsions=flatten_torsions)

            if not topology_proposal.unique_new_atoms:
                assert geometry_engine.forward_final_context_reduced_potential == None, f"There are no unique new atoms but the geometry_engine's final context reduced potential is not None (i.e. {self._geometry_engine.forward_final_context_reduced_potential})"
                assert geometry_engine.forward_atoms_with_positions_reduced_potential == None, f"There are no unique new atoms but the geometry_engine's forward atoms-with-positions-reduced-potential in not None (i.e. { self._geometry_engine.forward_atoms_with_positions_reduced_potential})"
            else:
                added_valence_energy = geometry_engine.forward_final_context_reduced_potential - geometry_engine.forward_atoms_with_positions_reduced_potential

            if not topology_proposal.unique_old_atoms:
                assert geometry_engine.reverse_final_context_reduced_potential == None, f"There are no unique old atoms but the geometry_engine's final context reduced potential is not None (i.e. {self._geometry_engine.reverse_final_context_reduced_potential})"
                assert geometry_engine.reverse_atoms_with_positions_reduced_potential == None, f"There are no unique old atoms but the geometry_engine's atoms-with-positions-reduced-potential in not None (i.e. { self._geometry_engine.reverse_atoms_with_positions_reduced_potential})"
                subtracted_valence_energy = 0.0
            else:
                subtracted_valence_energy = geometry_engine.reverse_final_context_reduced_potential - geometry_engine.reverse_atoms_with_positions_reduced_potential


            if conduct_endstate_validation and repartitioned_endstate is None:
                zero_state_error, one_state_error = validate_endstate_energies(forward_htf._topology_proposal, forward_htf, added_valence_energy, subtracted_valence_energy, beta=beta, ENERGY_THRESHOLD=ENERGY_THRESHOLD)
                if zero_state_error > ENERGY_THRESHOLD:
                    _logger.warning(f"Reduced potential difference of the nonalchemical and alchemical Lambda = 0 state is above the threshold ({ENERGY_THRESHOLD}): {zero_state_error}")
                if one_state_error > ENERGY_THRESHOLD:
                    _logger.warning(f"Reduced potential difference of the nonalchemical and alchemical Lambda = 1 state is above the threshold ({ENERGY_THRESHOLD}): {one_state_error}")
            else:
                pass

            htfs.append(forward_htf)

        self.apo_htf = htfs[0]
        self.complex_htf = htfs[1] if ligand_input else None

    def get_complex_htf(self):
        return self.complex_htf

    def get_apo_htf(self):
        return self.apo_htf


    def _solvate(self,
               topology,
               positions,
               water_model,
               phase,
               ionic_strength,
               box_dimensions=None):
        """
        Generate a solvated topology, positions, and system for a given input topology and positions.
        For generating the system, the forcefield files provided in the constructor will be used.

        Parameters
        ----------
        topology : app.Topology
            Topology of the system to solvate
        positions : [n, 3] ndarray of Quantity nm
            the positions of the unsolvated system
        forcefield : SystemGenerator.forcefield
            forcefield file of solvent to add
        water_model : str
            solvent model to use for solvation
        phase : str
            if phase == vacuum, then the complex will not be solvated with water; else, it will be solvated with tip3p
        ionic_strength : float * unit.molar
            the total concentration of ions (both positive and negative) to add using Modeller.
            This does not include ions that are added to neutralize the system.
            Note that only monovalent ions are currently supported.

        Returns
        -------
        solvated_topology : app.Topology
            Topology of the system with added waters
        solvated_positions : [n + 3(n_waters), 3] ndarray of Quantity nm
            Solvated positions
        solvated_system : openmm.System
            The parameterized system, containing a barostat if one was specified.
        """
        modeller = app.Modeller(topology, positions)

        # Now we have to add missing atoms
        if phase != 'vacuum':
            _logger.info(f"solvating at {ionic_strength} using {water_model}")
            if not box_dimensions:
                modeller.addSolvent(self.system_generator.forcefield, model=water_model, padding=0.9 * unit.nanometers, ionicStrength=ionic_strength)
            else:
                modeller.addSolvent(self.system_generator.forcefield, model=water_model, boxSize=box_dimensions, ionicStrength=ionic_strength)
        else:
            pass

        solvated_topology = modeller.getTopology()
        if box_dimensions:
            solvated_topology.setUnitCellDimensions(box_dimensions)
        solvated_positions = modeller.getPositions()

        # Canonicalize the solvated positions: turn tuples into np.array
        solvated_positions = unit.quantity.Quantity(value=np.array([list(atom_pos) for atom_pos in solvated_positions.value_in_unit_system(unit.md_unit_system)]), unit=unit.nanometers)
        solvated_system = self.system_generator.create_system(solvated_topology)

        return solvated_topology, solvated_positions, solvated_system
