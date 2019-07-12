from __future__ import absolute_import

from perses.dispersed import feptasks
from perses.utils.openeye import *
from perses.utils.data import load_smi
from perses.annihilation.relative import HybridTopologyFactory
from perses.annihilation.lambda_protocol import RelativeAlchemicalState
from perses.rjmc.topology_proposal import TopologyProposal, TwoMoleculeSetProposalEngine, SystemGenerator,SmallMoleculeSetProposalEngine
from perses.rjmc.geometry import FFAllAngleGeometryEngine

from openmmtools.states import ThermodynamicState, CompoundThermodynamicState, SamplerState

import pymbar
import simtk.openmm as openmm
import simtk.openmm.app as app
import simtk.unit as unit
import numpy as np
from openmoltools import forcefield_generators
import copy
import mdtraj as md
from io import StringIO
from openmmtools.constants import kB
import logging
import os
import dask.distributed as distributed
import parmed as pm

logging.basicConfig(level = logging.NOTSET)
_logger = logging.getLogger("relative_setup")
_logger.setLevel(logging.INFO)



class RelativeFEPSetup(object):
    """
    This class is a helper class for relative FEP calculations. It generates the input objects that are necessary
    legs of a relative FEP calculation. For each leg, that is a TopologyProposal, old_positions, and new_positions.
    Importantly, it ensures that the atom maps in the solvent and complex phases match correctly.
    """
    def __init__(self, ligand_input, old_ligand_index, new_ligand_index, forcefield_files, phases,
                 protein_pdb_filename=None,receptor_mol2_filename=None, pressure=1.0 * unit.atmosphere,
                 temperature=300.0 * unit.kelvin, solvent_padding=9.0 * unit.angstroms, atom_map=None,
                 hmass=4*unit.amus, neglect_angles = False):
        """
        Initialize a NonequilibriumFEPSetup object

        Parameters
        ----------
        ligand_input : str
            the name of the ligand file (any openeye supported format)
            this can either be an .sdf or list of .sdf files, or a list of SMILES strings
        forcefield_files : list of str
            The list of ffxml files that contain the forcefields that will be used
        phases : list of str
            The phases to simulate
        protein_pdb_filename : str, default None
            Protein pdb filename. If none, receptor_mol2_filename must be provided
        receptor_mol2_filename : str, default None
            Receptor mol2 filename. If none, protein_pdb_filename must be provided
        pressure : Quantity, units of pressure
            Pressure to use in the barostat
        temperature : Quantity, units of temperature
            Temperature to use for the Langevin integrator
        solvent_padding : Quantity, units of length
            The amount of padding to use when adding solvent
        neglect_angles : bool
            Whether to neglect certain angle terms for the purpose of minimizing work variance in the RJMC protocol.
        """
        self._pressure = pressure
        self._temperature = temperature
        self._barostat_period = 50
        self._padding = solvent_padding
        self._hmass = hmass
        _logger.info(f"\t\t\t_hmass: {hmass}.\n")
        self._proposal_phase = None

        beta = 1.0 / (kB * temperature)

        mol_list = []

        #all legs need ligands so do this first
        self._ligand_input = ligand_input
        self._old_ligand_index = old_ligand_index
        self._new_ligand_index = new_ligand_index
        _logger.info(f"Handling files for ligands and indices...")
        if type(self._ligand_input) is not list: # the ligand has been provided as a single file
            if self._ligand_input[-3:] == 'smi': #
                _logger.info(f"Detected .smi format.  Proceeding...")
                self._ligand_smiles_old = load_smi(self._ligand_input,self._old_ligand_index)
                self._ligand_smiles_new = load_smi(self._ligand_input,self._new_ligand_index)
                _logger.info(f"\told smiles: {self._ligand_smiles_old}")
                _logger.info(f"\tnew smiles: {self._ligand_smiles_new}")

                all_old_mol = createSystemFromSMILES(self._ligand_smiles_old,title='MOL')
                self._ligand_oemol_old, self._ligand_system_old, self._ligand_positions_old, self._ligand_topology_old = all_old_mol

                all_new_mol = createSystemFromSMILES(self._ligand_smiles_new,title='NEW')
                self._ligand_oemol_new, self._ligand_system_new, self._ligand_positions_new, self._ligand_topology_new = all_new_mol
                _logger.info(f"\tsuccessfully created old and new systems from smiles")

                mol_list.append(self._ligand_oemol_old)
                mol_list.append(self._ligand_oemol_new)

                ffxml = forcefield_generators.generateForceFieldFromMolecules(mol_list)
                _logger.info(f"\tsuccessfully generated ffxml from molecules.")

                # forcefield_generators needs to be able to distinguish between the two ligands
                # while topology_proposal needs them to have the same residue name
                self._ligand_oemol_old.SetTitle("MOL")
                self._ligand_oemol_new.SetTitle("MOL")
                _logger.info(f"\tsetting both molecule oemol titles to 'MOL'.")

                self._ligand_topology_old = forcefield_generators.generateTopologyFromOEMol(self._ligand_oemol_old)
                self._ligand_topology_new = forcefield_generators.generateTopologyFromOEMol(self._ligand_oemol_new)
                _logger.info(f"\tsuccessfully generated topologies for both oemols.")

            elif self._ligand_input[-3:] == 'sdf': #
                _logger.info(f"Detected .sdf format.  Proceeding...") #TODO: write checkpoints for sdf format
                self._ligand_oemol_old = createOEMolFromSDF(self._ligand_input, index=self._old_ligand_index)
                self._ligand_oemol_new = createOEMolFromSDF(self._ligand_input, index=self._new_ligand_index)

                mol_list.append(self._ligand_oemol_old)
                mol_list.append(self._ligand_oemol_new)

                self._ligand_positions_old = extractPositionsFromOEMol(self._ligand_oemol_old)
                _logger.info(f"\tsuccessfully extracted positions from OEMOL.")

                ffxml = forcefield_generators.generateForceFieldFromMolecules(mol_list)
                _logger.info(f"\tsuccessfully generated ffxml from molecules.")

                self._ligand_oemol_old.SetTitle("MOL")
                self._ligand_oemol_new.SetTitle("MOL")
                _logger.info(f"\tsetting both molecule oemol titles to 'MOL'.")

                self._ligand_smiles_old = oechem.OECreateSmiString(self._ligand_oemol_old,
                            oechem.OESMILESFlag_DEFAULT | oechem.OESMILESFlag_Hydrogens)
                self._ligand_smiles_new = oechem.OECreateSmiString(self._ligand_oemol_new,
                            oechem.OESMILESFlag_DEFAULT | oechem.OESMILESFlag_Hydrogens)
                _logger.info(f"\tsuccessfully created SMILES for both ligand OEMOLs.")

                # replace this with function that will generate the system etc. so that vacuum can be performed
                self._ligand_topology_old = forcefield_generators.generateTopologyFromOEMol(self._ligand_oemol_old)
                self._ligand_topology_new = forcefield_generators.generateTopologyFromOEMol(self._ligand_oemol_new)
                _logger.info(f"\tsuccessfully generated topologies for both OEMOLs.")
            else:
                print(f'RelativeFEPSetup can only handle .smi or .sdf files currently')

        else: # the ligand has been provided as a list of .sdf files
            _logger.info(f"Detected list...perhaps this is of sdf format.  Proceeding (but without checkpoints...this may be buggy).") #TODO: write checkpoints and debug for list
            old_ligand = pm.load_file('%s.parm7' % self._ligand_input[0], '%s.rst7' % self._ligand_input[0])
            self._ligand_topology_old = old_ligand.topology
            self._ligand_positions_old = old_ligand.positions
            self._ligand_oemol_old = createOEMolFromSDF('%s.mol2' % self._ligand_input[0])
            self._ligand_smiles_old = oechem.OECreateSmiString(self._ligand_oemol_old,
                                                             oechem.OESMILESFlag_DEFAULT | oechem.OESMILESFlag_Hydrogens)

            new_ligand = pm.load_file('%s.parm7' % self._ligand_input[1], '%s.rst7' % self._ligand_input[1])
            self._ligand_topology_new = new_ligand.topology
            self._ligand_positions_new = new_ligand.positions
            self._ligand_oemol_new = createOEMolFromSDF('%s.mol2' % self._ligand_input[1])
            self._ligand_smiles_new = oechem.OECreateSmiString(self._ligand_oemol_new,
                                                             oechem.OESMILESFlag_DEFAULT | oechem.OESMILESFlag_Hydrogens)

            mol_list.append(self._ligand_oemol_old)
            mol_list.append(self._ligand_oemol_new)

            old_ligand_parameter_set = pm.openmm.OpenMMParameterSet.from_structure(old_ligand)
            new_ligand_parameter_set = pm.openmm.OpenMMParameterSet.from_structure(new_ligand)
            ffxml = StringIO()
            old_ligand_parameter_set.write(ffxml)
            new_ligand_parameter_set.write(ffxml)
            ffxml = ffxml.getvalue()

        self._ligand_md_topology_old = md.Topology.from_openmm(self._ligand_topology_old)
        self._ligand_md_topology_new = md.Topology.from_openmm(self._ligand_topology_new)
        _logger.info(f"Created mdtraj topologies for both ligands.")

        if 'complex' in phases or 'solvent' in phases:
            self._nonbonded_method = app.PME
            _logger.info(f"Detected complex or solvent phases: setting PME nonbonded method.")
        elif 'vacuum' in phases:
            self._nonbonded_method = app.NoCutoff
            _logger.info(f"Detected vacuum phase: setting noCutoff nonbonded method.")

        if pressure is not None:
            if self._nonbonded_method == app.PME:
                barostat = openmm.MonteCarloBarostat(self._pressure, self._temperature, self._barostat_period)
                _logger.info(f"set MonteCarloBarostat.")
            else:
                barostat = None
                _logger.info(f"omitted MonteCarloBarostat.")
            self._system_generator = SystemGenerator(forcefield_files, barostat=barostat,
                                                     forcefield_kwargs={'removeCMMotion': False, 'nonbondedMethod': self._nonbonded_method,'constraints' : app.HBonds, 'hydrogenMass' : self._hmass})
        else:
            self._system_generator = SystemGenerator(forcefield_files, forcefield_kwargs={'removeCMMotion': False,'nonbondedMethod': self._nonbonded_method,'constraints' : app.HBonds, 'hydrogenMass' : self._hmass})

        _logger.info("successfully called TopologyProposal.SystemGenerator to create ligand systems.")
        self._system_generator._forcefield.loadFile(StringIO(ffxml))

        _logger.info(f"executing SmallMoleculeSetProposalEngine...")
        self._proposal_engine = SmallMoleculeSetProposalEngine([self._ligand_smiles_old, self._ligand_smiles_new], self._system_generator, residue_name='MOL')

        _logger.info(f"instantiating FFAllAngleGeometryEngine...")
        # NOTE: we are conducting the geometry proposal without any neglected angles
        self._geometry_engine = FFAllAngleGeometryEngine(metadata=None, use_sterics=False, n_bond_divisions=100, n_angle_divisions=180, n_torsion_divisions=360, verbose=True, storage=None, bond_softening_constant=1.0, angle_softening_constant=1.0, neglect_angles = neglect_angles)
        if 'complex' in phases: self._complex_geometry_engine = copy.deepcopy(self._geometry_engine)
        if 'solvent' in phases: self._solvent_geometry_engine = copy.deepcopy(self._geometry_engine)
        if 'vacuum' in phases: self._vacuum_geometry_engine = copy.deepcopy(self._geometry_engine)


        # if we are running multiple phases, we only want to generate one topology proposal, and use the same one for the other legs
        # this is tracked using _proposal_phase
        if 'complex' in phases:
            _logger.info('Generating the topology proposal from the complex leg')
            self._nonbonded_method = app.PME
            _logger.info(f"setting up complex phase...")
            self._setup_complex_phase(protein_pdb_filename,receptor_mol2_filename,mol_list)
            self._complex_topology_old_solvated, self._complex_positions_old_solvated, self._complex_system_old_solvated = self._solvate_system(
            self._complex_topology_old, self._complex_positions_old)
            _logger.info(f"successfully generated complex topology, positions, system")

            self._complex_md_topology_old_solvated = md.Topology.from_openmm(self._complex_topology_old_solvated)

            _logger.info(f"creating TopologyProposal...")
            self._complex_topology_proposal = self._proposal_engine.propose(self._complex_system_old_solvated,
                                                                                self._complex_topology_old_solvated, current_mol=self._ligand_oemol_old,proposed_mol=self._ligand_oemol_new)
            self.non_offset_new_to_old_atom_map = self._proposal_engine.non_offset_new_to_old_atom_map

            self._proposal_phase = 'complex'

            _logger.info(f"conducting geometry proposal...")
            self._complex_positions_new_solvated, self._complex_logp_proposal = self._complex_geometry_engine.propose(self._complex_topology_proposal,
                                                                                self._complex_positions_old_solvated,
                                                                                beta)
            self._complex_logp_reverse = self._complex_geometry_engine.logp_reverse(self._complex_topology_proposal, self._complex_positions_new_solvated, self._complex_positions_old_solvated, beta)
            self._complex_added_valence_energy = self._complex_geometry_engine.forward_final_context_reduced_potential - self._complex_geometry_engine.forward_atoms_with_positions_reduced_potential
            self._complex_subtracted_valence_energy = self._complex_geometry_engine.reverse_final_context_reduced_potential - self._complex_geometry_engine.reverse_atoms_with_positions_reduced_potential
            self._complex_forward_neglected_angles = self._complex_geometry_engine.forward_neglected_angle_terms
            self._complex_reverse_neglected_angles = self._complex_geometry_engine.reverse_neglected_angle_terms


        if 'solvent' in phases:
            _logger.info(f"Detected solvent...")
            if self._proposal_phase is None:
                _logger.info(f"no complex detected in phases...generating unique topology/geometry proposals...")
                self._nonbonded_method = app.PME
                _logger.info(f"solvating ligand...")
                self._ligand_topology_old_solvated, self._ligand_positions_old_solvated, self._ligand_system_old_solvated = self._solvate_system(
                self._ligand_topology_old, self._ligand_positions_old)
                self._ligand_md_topology_old_solvated = md.Topology.from_openmm(self._ligand_topology_old_solvated)

                _logger.info(f"creating TopologyProposal")
                self._solvent_topology_proposal = self._proposal_engine.propose(self._ligand_system_old_solvated,
                                                                                    self._ligand_topology_old_solvated,current_mol=self._ligand_oemol_old,proposed_mol=self._ligand_oemol_new)
                self.non_offset_new_to_old_atom_map = self._proposal_engine.non_offset_new_to_old_atom_map
                self._proposal_phase = 'solvent'
            else:
                _logger.info('Using the topology proposal from the complex leg')
                self._solvent_topology_proposal, self._ligand_positions_old_solvated = self._generate_solvent_topologies(
                    self._complex_topology_proposal, self._complex_positions_old_solvated)

            _logger.info(f"conducting geometry proposal...")
            self._ligand_positions_new_solvated, self._ligand_logp_proposal_solvated = self._solvent_geometry_engine.propose(self._solvent_topology_proposal,
                                                                                    self._ligand_positions_old_solvated, beta)
            self._ligand_logp_reverse_solvated = self._solvent_geometry_engine.logp_reverse(self._solvent_topology_proposal, self._ligand_positions_new_solvated, self._ligand_positions_old_solvated, beta)
            self._solvated_added_valence_energy = self._solvent_geometry_engine.forward_final_context_reduced_potential - self._solvent_geometry_engine.forward_atoms_with_positions_reduced_potential
            self._solvated_subtracted_valence_energy = self._solvent_geometry_engine.reverse_final_context_reduced_potential - self._solvent_geometry_engine.reverse_atoms_with_positions_reduced_potential
            self._solvated_forward_neglected_angles = self._solvent_geometry_engine.forward_neglected_angle_terms
            self._solvated_reverse_neglected_angles = self._solvent_geometry_engine.reverse_neglected_angle_terms

        if 'vacuum' in phases:
            _logger.info(f"Detected solvent...")
            # need to change nonbonded cutoff and remove barostat for vacuum leg
            _logger.info(f"assgning noCutoff to nonbonded_method")
            self._nonbonded_method = app.NoCutoff
            _logger.info(f"calling TopologyProposal.SystemGenerator to create ligand systems.")
            self._system_generator = SystemGenerator(forcefield_files, forcefield_kwargs={'removeCMMotion': False,
                                                    'nonbondedMethod': self._nonbonded_method,'constraints' : app.HBonds})
            self._system_generator._forcefield.loadFile(StringIO(ffxml))
            if self._proposal_phase is None:
                _logger.info('No complex or solvent leg, so performing topology proposal for vacuum leg')
                self._vacuum_topology_old, self._vacuum_positions_old, self._vacuum_system_old = self._solvate_system(self._ligand_topology_old,
                                                                                                         self._ligand_positions_old,vacuum=True)
                self._vacuum_topology_proposal = self._proposal_engine.propose(self._vacuum_system_old,
                                                                                self._vacuum_topology_old,current_mol=self._ligand_oemol_old,proposed_mol=self._ligand_oemol_new)
                self.non_offset_new_to_old_atom_map = self._proposal_engine.non_offset_new_to_old_atom_map
                self._proposal_phase = 'vacuum'
            elif self._proposal_phase == 'complex':
                _logger.info('Using the topology proposal from the complex leg')
                self._vacuum_topology_proposal, self._vacuum_positions_old = self._generate_vacuum_topologies(
                    self._complex_topology_proposal, self._complex_positions_old_solvated)
            elif self._proposal_phase == 'solvent':
                _logger.info('Using the topology proposal from the solvent leg')
                self._vacuum_topology_proposal, self._vacuum_positions_old = self._generate_vacuum_topologies(
                    self._solvent_topology_proposal, self._ligand_positions_old_solvated)

            _logger.info(f"conducting geometry proposal...")
            self._vacuum_positions_new, self._vacuum_logp_proposal = self._vacuum_geometry_engine.propose(self._vacuum_topology_proposal,
                                                                          self._vacuum_positions_old,
                                                                          beta)
            self._vacuum_logp_reverse = self._vacuum_geometry_engine.logp_reverse(self._vacuum_topology_proposal, self._vacuum_positions_new, self._vacuum_positions_old, beta)
            self._vacuum_added_valence_energy = self._vacuum_geometry_engine.forward_final_context_reduced_potential - self._vacuum_geometry_engine.forward_atoms_with_positions_reduced_potential
            self._vacuum_subtracted_valence_energy = self._vacuum_geometry_engine.reverse_final_context_reduced_potential - self._vacuum_geometry_engine.reverse_atoms_with_positions_reduced_potential
            self._vacuum_forward_neglected_angles = self._vacuum_geometry_engine.forward_neglected_angle_terms
            self._vacuum_reverse_neglected_angles = self._vacuum_geometry_engine.reverse_neglected_angle_terms

    def _setup_complex_phase(self,protein_pdb_filename,receptor_mol2_filename,mol_list):
        """
        Runs setup on the protein/receptor file for relative simulations

        Parameters
        ----------
        protein_pdb_filename : str, default None
            Protein pdb filename. If none, receptor_mol2_filename must be provided
        receptor_mol2_filename : str, default None
            Receptor mol2 filename. If none, protein_pdb_filename must be provided
        """
        if protein_pdb_filename:
            self._protein_pdb_filename = protein_pdb_filename
            protein_pdbfile = open(self._protein_pdb_filename, 'r')
            pdb_file = app.PDBFile(protein_pdbfile)
            protein_pdbfile.close()
            self._receptor_positions_old = pdb_file.positions
            self._receptor_topology_old = pdb_file.topology
            self._receptor_md_topology_old = md.Topology.from_openmm(self._receptor_topology_old)

        elif receptor_mol2_filename:
            self._receptor_mol2_filename = receptor_mol2_filename
            self._receptor_mol = createOEMolFromSDF(self._receptor_mol2_filename)
            mol_list.append(self._receptor_mol)
            self._receptor_positions_old = extractPositionsFromOEMol(self._receptor_mol)
            self._receptor_topology_old = forcefield_generators.generateTopologyFromOEMol(self._receptor_mol)
            self._receptor_md_topology_old = md.Topology.from_openmm(self._receptor_topology_old)
        else:
            raise ValueError("You need to provide either a protein pdb or a receptor mol2 to run a complex simulation.")

        self._complex_md_topology_old = self._receptor_md_topology_old.join(self._ligand_md_topology_old)
        self._complex_topology_old = self._complex_md_topology_old.to_openmm()

        n_atoms_complex_old = self._complex_topology_old.getNumAtoms()
        n_atoms_protein_old = self._receptor_topology_old.getNumAtoms()

        self._complex_positions_old = unit.Quantity(np.zeros([n_atoms_complex_old, 3]), unit=unit.nanometers)
        self._complex_positions_old[:n_atoms_protein_old, :] = self._receptor_positions_old
        self._complex_positions_old[n_atoms_protein_old:, :] = self._ligand_positions_old

    def _generate_solvent_topologies(self, topology_proposal, old_positions):
        """
        This method generates ligand-only topologies and positions from a TopologyProposal containing a solvated complex.
        The output of this method is then used when building the solvent-phase simulation with the same atom map.

        Parameters
        ----------
        old_positions : array
            Positions of the fully solvated protein ligand syste

        Returns
        -------
        ligand_topology_proposal : perses.rjmc.topology_proposal.TopologyProposal
            Topology proposal object of the ligand without complex

        old_solvated_positions : array
            positions of the system without complex
        """
        old_complex = md.Topology.from_openmm(topology_proposal.old_topology)
        new_complex = md.Topology.from_openmm(topology_proposal.new_topology)

        atom_map = topology_proposal.old_to_new_atom_map

        old_mol_start_index, old_mol_len = self._proposal_engine._find_mol_start_index(old_complex.to_openmm())
        new_mol_start_index, new_mol_len = self._proposal_engine._find_mol_start_index(new_complex.to_openmm())

        old_pos = unit.Quantity(np.zeros([len(old_positions), 3]), unit=unit.nanometers)
        old_pos[:, :] = old_positions
        old_ligand_positions = old_pos[old_mol_start_index:(old_mol_start_index + old_mol_len), :]

        # subset the topologies:
        old_ligand_topology = old_complex.subset(old_complex.select("resname == 'MOL' "))
        new_ligand_topology = new_complex.subset(new_complex.select("resname == 'MOL' "))

        # solvate the old ligand topology:
        old_solvated_topology, old_solvated_positions, old_solvated_system = self._solvate_system(
            old_ligand_topology.to_openmm(), old_ligand_positions)

        old_solvated_md_topology = md.Topology.from_openmm(old_solvated_topology)

        # now remove the old ligand, leaving only the solvent
        solvent_only_topology = old_solvated_md_topology.subset(old_solvated_md_topology.select("not resname MOL"))
        # append the solvent to the new ligand-only topology:
        new_solvated_ligand_md_topology = new_ligand_topology.join(solvent_only_topology)
        nsl, b = new_solvated_ligand_md_topology.to_dataframe()

        # dirty hack because new_solvated_ligand_md_topology.to_openmm() was throwing bond topology error
        new_solvated_ligand_md_topology = md.Topology.from_dataframe(nsl, b)

        new_solvated_ligand_omm_topology = new_solvated_ligand_md_topology.to_openmm()
        new_solvated_ligand_omm_topology.setPeriodicBoxVectors(old_solvated_topology.getPeriodicBoxVectors())

        # create the new ligand system:
        new_solvated_system = self._system_generator.build_system(new_solvated_ligand_omm_topology)

        new_to_old_atom_map = {atom_map[x] - new_mol_start_index: x - old_mol_start_index for x in
                               old_complex.select("resname == 'MOL' ") if x in atom_map.keys()}

        # adjust the atom map to account for the presence of solvent degrees of freedom:
        # By design, all atoms after the ligands are water, and should be mapped.
        n_water_atoms = solvent_only_topology.to_openmm().getNumAtoms()
        for i in range(n_water_atoms):
            new_to_old_atom_map[new_mol_len + i] = old_mol_len + i

        # make a TopologyProposal
        ligand_topology_proposal = TopologyProposal(new_topology=new_solvated_ligand_omm_topology,
                                                    new_system=new_solvated_system,
                                                    old_topology=old_solvated_topology, old_system=old_solvated_system,
                                                    new_to_old_atom_map=new_to_old_atom_map, old_chemical_state_key='A',
                                                    new_chemical_state_key='B')

        return ligand_topology_proposal, old_solvated_positions

    def _generate_vacuum_topologies(self, topology_proposal, old_positions):
        """
        This method generates ligand-only topologies and positions from a TopologyProposal containing a solvated complex.
        The output of this method is then used when building the solvent-phase simulation with the same atom map.

        Parameters
        ----------
        old_positions : array
            Positions of the fully solvated protein ligand syste

        Returns
        -------
        ligand_topology_proposal : perses.rjmc.topology_proposal.TopologyProposal
            Topology proposal object of the ligand without complex

        old_solvated_positions : array
            positions of the system without complex
        """
        old_complex = md.Topology.from_openmm(topology_proposal.old_topology)
        new_complex = md.Topology.from_openmm(topology_proposal.new_topology)

        atom_map = topology_proposal.old_to_new_atom_map

        old_mol_start_index, old_mol_len = self._proposal_engine._find_mol_start_index(old_complex.to_openmm())
        new_mol_start_index, new_mol_len = self._proposal_engine._find_mol_start_index(new_complex.to_openmm())

        old_pos = unit.Quantity(np.zeros([len(old_positions), 3]), unit=unit.nanometers)
        old_pos[:, :] = old_positions
        old_ligand_positions = old_pos[old_mol_start_index:(old_mol_start_index + old_mol_len), :]

        # subset the topologies:
        old_ligand_topology = old_complex.subset(old_complex.select("resname == 'MOL' "))
        new_ligand_topology = new_complex.subset(new_complex.select("resname == 'MOL' "))

        # convert to openmm topology object
        old_ligand_topology = old_ligand_topology.to_openmm()
        new_ligand_topology = new_ligand_topology.to_openmm()

        # create the new ligand system:
        old_ligand_system = self._system_generator.build_system(old_ligand_topology)
        new_ligand_system = self._system_generator.build_system(new_ligand_topology)

        new_to_old_atom_map = {atom_map[x] - new_mol_start_index: x - old_mol_start_index for x in
                               old_complex.select("resname == 'MOL' ") if x in atom_map.keys()}


        # make a TopologyProposal
        ligand_topology_proposal = TopologyProposal(new_topology=new_ligand_topology,
                                                    new_system=new_ligand_system,
                                                    old_topology=old_ligand_topology, old_system=old_ligand_system,
                                                    new_to_old_atom_map=new_to_old_atom_map, old_chemical_state_key='A',
                                                    new_chemical_state_key='B')

        return ligand_topology_proposal, old_ligand_positions

    def _solvate_system(self, topology, positions, model='tip3p',vacuum=False):
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
        model : str, default 'tip3p'
            solvent model to use for solvation

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
        hs = [atom for atom in modeller.topology.atoms() if atom.element.symbol in ['H'] and atom.residue.name not in ['MOL','OLD','NEW']]
        modeller.delete(hs)
        modeller.addHydrogens(forcefield=self._system_generator._forcefield)
        if not vacuum:
            _logger.info(f"\tpreparing to add solvent")
            modeller.addSolvent(self._system_generator._forcefield, model=model, padding=self._padding)
        else:
            _logger.info(f"\tSkipping solvation of vacuum perturbation")
        solvated_topology = modeller.getTopology()
        solvated_positions = modeller.getPositions()

        # canonicalize the solvated positions: turn tuples into np.array
        solvated_positions = unit.quantity.Quantity(value = np.array([list(atom_pos) for atom_pos in solvated_positions.value_in_unit_system(unit.md_unit_system)]), unit = unit.nanometers)
        _logger.info(f"\tparameterizing...")
        solvated_system = self._system_generator.build_system(solvated_topology)
        _logger.info(f"\tSystem parameterized")
        return solvated_topology, solvated_positions, solvated_system

    @property
    def complex_topology_proposal(self):
        return self._complex_topology_proposal

    @property
    def complex_old_positions(self):
        return self._complex_positions_old_solvated

    @property
    def complex_new_positions(self):
        return self._complex_positions_new_solvated

    @property
    def solvent_topology_proposal(self):
        return self._solvent_topology_proposal

    @property
    def solvent_old_positions(self):
        return self._ligand_positions_old_solvated

    @property
    def solvent_new_positions(self):
        return self._ligand_positions_new_solvated

    @property
    def vacuum_topology_proposal(self):
        return self._vacuum_topology_proposal

    @property
    def vacuum_old_positions(self):
        return self._vacuum_positions_old

    @property
    def vacuum_new_positions(self):
        return self._vacuum_positions_new



class NonequilibriumSwitchingFEP(object):
    """
    This class manages Nonequilibrium switching based relative free energy calculations, carried out on a distributed computing framework.
    """

    def __init__(self, topology_proposal, pos_old, new_positions, use_dispersion_correction=False,
                 forward_functions=None, n_equil_steps=1000, ncmc_nsteps=100, nsteps_per_iteration=1,
                 temperature=300.0 * unit.kelvin, trajectory_directory=None, trajectory_prefix=None,
                 atom_selection="not water", scheduler_address=None, eq_splitting_string="V R O R V", neq_splitting_string="V R O H R V", measure_shadow_work=False, timestep=1.0*unit.femtoseconds,
                 neglected_new_angle_terms = neglected_new_angle_terms, neglected_old_angle_terms = neglected_old_angle_terms):
        """
        Create an instance of the NonequilibriumSwitchingFEP driver class

        Parameters
        ----------
        topology_proposal : perses.rjmc.topology_proposal.TopologyProposal
            TopologyProposal object containing transformation of interest
        pos_old : [n, 3] ndarray unit.Quantity
            Positions of the old system.
        new_positions : [m, 3] ndarray unit.Quantity
            Positions of the new system
        use_dispersion_correction : bool, default False
            Whether to use the (expensive) dispersion correction
        forward_functions : dict of str: str, default None
            How each force's scaling parameter relates to the main lambda that is switched by the integrator.
        n_equil_steps : int, default 1000
            Number of equilibrium steps between switching events
        ncmc_nsteps : int, default 100
            Number of steps per NCMC trajectory
        nsteps_per_iteration : int, default one
            Number of steps to take per MCMove; this controls how often configurations are written out.
        temperature : float unit.Quantity
            Temperature at which to perform the simulation, default 300K
        trajectory_directory : str, default None
            Where to write out trajectories resulting from the calculation. If none, no writing is done.
        trajectory_prefix : str, default None
            What prefix to use for this calculation's trajectory files. If none, no writing is done.
        atom_selection : str, default not water
            MDTraj selection syntax for which atomic coordinates to save in the trajectories. Default strips
            all water.
        scheduler_address : str, default None
            The address of the dask scheduler. If None, local will be used.
        eq_splitting_string : str, default V R O R V
            The integrator splitting to use for equilibrium simulation
        neq_splitting_string : str, default V R O H R V
            The integrator splitting to use for the nonequilibrium simulation
        neglected_new_angle_terms : list
            list of indices from the HarmonicAngleForce of the new_system for which the geometry engine neglected.
            Hence, these angles must be alchemically grown in for the unique new atoms (forward lambda protocol)
        neglected_old_angle_terms : list
            list of indices from the HarmonicAngleForce of the old_system for which the geometry engine neglected.
            Hence, these angles must be alchemically deleted for the unique old atoms (reverse lambda protocol)
        """
        if scheduler_address is None:
            self._map = map
            self._gather = lambda mapped_list: list(mapped_list)
        else:
            _logger.info(f"scheduler address is localhost")
            if scheduler_address == 'localhost':
                self._client = distributed.Client()
            else:
                self._client = distributed.Client(scheduler_address)
            self._map = self._client.map
            self._gather = self._client.gather

        # construct the hybrid topology factory object
        _logger.info(f"writing HybridTopologyFactories")
        self._factory = HybridTopologyFactory(topology_proposal, pos_old, new_positions, neglected_new_angle_terms, neglected_old_angle_terms)

        # setup splitting string:
        self._neq_splitting_string = neq_splitting_string
        self._eq_splitting_string = eq_splitting_string

        self._measure_shadow_work = measure_shadow_work

        # set up some class attributes
        self._hybrid_system = self._factory.hybrid_system
        self._initial_hybrid_positions = self._factory.hybrid_positions
        self._ncmc_nsteps = ncmc_nsteps
        self._nsteps_per_iteration = nsteps_per_iteration
        self._trajectory_prefix = trajectory_prefix
        self._trajectory_directory = trajectory_directory
        self._zero_endpoint_n_atoms = topology_proposal.n_atoms_old
        self._one_endpoint_n_atoms = topology_proposal.n_atoms_new
        self._atom_selection = atom_selection
        self._current_iteration = 0

        self._timestep = timestep

        _logger.info(f"instantiating trajectory filenames")
        if self._trajectory_directory and self._trajectory_prefix:
            self._write_traj = True
            self._trajectory_filename = {lambda_state: os.path.join(os.getcwd(), self._trajectory_directory,
                                                                    trajectory_prefix + "lambda%d" % lambda_state + ".h5")
                                         for lambda_state in [0, 1]}
            self._neq_traj_filename = {lambda_state: os.path.join(os.getcwd(), self._trajectory_directory,
                                                                  trajectory_prefix + ".{iteration}.neq.lambda%d" % lambda_state + ".h5")
                                       for lambda_state in [0, 1]}
        else:
            self._write_traj = False
            self._trajectory_filename = {0: None, 1: None}
            self._neq_traj_filename = {0: None, 1: None}

        # initialize lists for results
        self._total_work = {0: [], 1: []}
        self._reduced_potential_differences = {0: [], 1: []}

        # Set the number of times that the nonequilbrium move will have to be run in order to complete a protocol:
        if self._ncmc_nsteps % self._nsteps_per_iteration != 0:
            logging.warning(
                "The number of ncmc steps is not divisible by the number of steps per iteration. You may not have a full protocol.")
        self._n_neq_iterations_per_call = self._ncmc_nsteps // self._nsteps_per_iteration

        # For now, we will not vary this.
        self._n_eq_iterations_per_call = 1

        # create the thermodynamic state
        _logger.info(f"Instantiating thermodynamic states.")
        lambda_zero_alchemical_state = RelativeAlchemicalState.from_system(self._hybrid_system)
        lambda_one_alchemical_state = copy.deepcopy(lambda_zero_alchemical_state)

        lambda_zero_alchemical_state.set_alchemical_parameters(0.0)
        lambda_one_alchemical_state.set_alchemical_parameters(1.0)

        # ensure their states are set appropriately
        self._hybrid_alchemical_states = {0: lambda_zero_alchemical_state, 1: lambda_one_alchemical_state}

        # create the base thermodynamic state with the hybrid system
        self._thermodynamic_state = ThermodynamicState(self._hybrid_system, temperature=temperature)

        # Create thermodynamic states for the nonalchemical endpoints
        self._nonalchemical_thermodynamic_states = {
            0: ThermodynamicState(topology_proposal.old_system, temperature=temperature),
            1: ThermodynamicState(topology_proposal.new_system, temperature=temperature)}

        # Now create the compound states with different alchemical states
        self._hybrid_thermodynamic_states = {0: CompoundThermodynamicState(self._thermodynamic_state,
                                                                           composable_states=[
                                                                               self._hybrid_alchemical_states[0]]),
                                             1: CompoundThermodynamicState(copy.deepcopy(self._thermodynamic_state),
                                                                           composable_states=[
                                                                               self._hybrid_alchemical_states[1]])}

        self._ncmc_nsteps = ncmc_nsteps
        self._temperature = temperature

        # create the equilibrium MCMove
        self._n_equil_steps = n_equil_steps

        # set the SamplerState for the lambda 0 and 1 equilibrium simulations
        _logger.info(f"Instantiating SamplerStates")
        self._lambda_one_sampler_state = SamplerState(self._initial_hybrid_positions,
                                                      box_vectors=self._hybrid_system.getDefaultPeriodicBoxVectors())
        self._lambda_zero_sampler_state = copy.deepcopy(self._lambda_one_sampler_state)

        self._sampler_states = {0: SamplerState(self._initial_hybrid_positions,
                                                box_vectors=self._hybrid_system.getDefaultPeriodicBoxVectors()),
                                1: copy.deepcopy(self._lambda_one_sampler_state)}

        # initialize by minimizing
        _logger.info(f"Instantiating equilibrium results by minimization")
        self._equilibrium_results = [feptasks.EquilibriumResult(result, 0.0) for result in self.minimize()]

        # subset the topology appropriately:
        if atom_selection is not None:
            atom_selection_indices = self._factory.hybrid_topology.select(atom_selection)
            self._atom_selection_indices = atom_selection_indices
        else:
            self._atom_selection_indices = None

        print("Constructed")

    def minimize(self, max_steps=50):
        """
        Minimize both end states. This method updates the _sampler_state attributes for each lambda

        Parameters
        ----------
        max_steps : int, default 50
            max number of steps for openmm minimizer.
        """
        minimized = self._map(feptasks.minimize, self._hybrid_thermodynamic_states.values(),
                              self._sampler_states.values())
        _logger.info("\tminimizing")
        return self._gather(minimized)

    def run(self, n_iterations=5):
        """
        Run one iteration of the nonequilibrium switching free energy calculations. This entails:

        - 1 iteration of equilibrium at lambda=0 and lambda=1
        - concurrency (parameter) many nonequilibrium trajectories in both forward and reverse
           (e.g., if concurrency is 5, then 5 forward and 5 reverse protocols will be run)
        - 1 iteration of equilibrium at lambda=0 and lambda=1

        Parameters
        ----------
        n_iterations : int, optional, default 5
            The number of times to run the entire sequence described above
        """
        endpoints = [0, 1]
        nsteps_equil = [self._n_equil_steps, self._n_equil_steps]
        hybrid_topology_list = [self._factory.hybrid_topology, self._factory.hybrid_topology]
        write_interval_list = [self._nsteps_per_iteration, self._nsteps_per_iteration]
        n_eq_iterations_per_call_list = [self._n_eq_iterations_per_call, self._n_eq_iterations_per_call]
        atom_indices_to_save_list = [self._atom_selection_indices, self._atom_selection_indices]
        hybrid_factory_list = [self._factory, self._factory]
        alchemical_functions = [self._forward_functions, self._reverse_functions]
        splitting = [self._neq_splitting_string, self._neq_splitting_string]
        eq_splitting = [self._eq_splitting_string, self._eq_splitting_string]
        nsteps_neq = [self._ncmc_nsteps, self._ncmc_nsteps]
        measure_shadow_work = [self._measure_shadow_work, self._measure_shadow_work]
        timestep = [self._timestep, self._timestep]
        write_configuration = [self._write_traj, self._write_traj]

        endpoint_perturbation_results_list = []
        nonequilibrium_results_list = []
        for i in range(n_iterations):

            if self._write_traj:
                equilibrium_trajectory_filenames = self._trajectory_filename.values()
                noneq_trajectory_filenames = [
                    self._neq_traj_filename[lambda_state].format(iteration=self._current_iteration) for lambda_state in
                    endpoints]
            else:
                equilibrium_trajectory_filenames = [None, None]
                noneq_trajectory_filenames = [None, None]

            # run a round of equilibrium
            self._equilibrium_results = self._gather(self._map(feptasks.run_equilibrium, self._equilibrium_results,
                                                               self._hybrid_thermodynamic_states.values(), nsteps_equil,
                                                               hybrid_topology_list, n_eq_iterations_per_call_list,
                                                               atom_indices_to_save_list,
                                                               equilibrium_trajectory_filenames, eq_splitting, timestep))

            # get the perturbations to nonalchemical states:
            endpoint_perturbation_results_mapped = self._map(feptasks.compute_nonalchemical_perturbation,
                                                             self._equilibrium_results, hybrid_factory_list,
                                                             self._nonalchemical_thermodynamic_states.values(),
                                                             endpoints)
            endpoint_perturbation_results_list.append(list(endpoint_perturbation_results_mapped))

            # run a round of nonequilibrium switching:
            nonequilibrium_results_list.append(
                self._map(feptasks.run_protocol, self._equilibrium_results, self._hybrid_thermodynamic_states.values(),
                          alchemical_functions, nsteps_neq, hybrid_topology_list, write_interval_list, splitting,
                          atom_indices_to_save_list, noneq_trajectory_filenames, write_configuration, timestep, measure_shadow_work))

            self._current_iteration += 1
            print(self._current_iteration)

        # after all tasks have been requested, retrieve the results:
        for i in range(n_iterations):
            self._equilibrium_results = self._gather(self._equilibrium_results)
            endpoint_perturbations = self._gather(endpoint_perturbation_results_list[i])
            nonequilibrium_results = self._gather(nonequilibrium_results_list[i])

            for lambda_state in [0, 1]:
                self._reduced_potential_differences[lambda_state].append(endpoint_perturbations[lambda_state])

                # for the nonequilibrium results, we have to access the last element of the cumulative work, since that
                # is the total work
                self._total_work[lambda_state].append(nonequilibrium_results[lambda_state].cumulative_work[-1])

    def equilibrate(self, n_iterations=100):
        """
        Run the equilibrium simulations a specified number of times without writing to a file. This can be used to equilibrate
        the simulation before beginning the free energy calculation.

        Parameters
        ----------
        n_iterations : int
            The number of times to apply the equilibrium MCMove
        """
        nsteps_equil = [self._n_equil_steps, self._n_equil_steps]
        hybrid_topology_list = [self._factory.hybrid_topology, self._factory.hybrid_topology]
        n_eq_iterations_per_call_list = [self._n_eq_iterations_per_call, self._n_eq_iterations_per_call]
        atom_indices_to_save_list = [self._atom_selection_indices, self._atom_selection_indices]
        eq_splitting = [self._eq_splitting_string, self._eq_splitting_string]
        timestep = [self._timestep, self._timestep]

        for i in range(n_iterations):

            if self._write_traj:
                equilibrium_trajectory_filenames = self._trajectory_filename.values()
            else:
                equilibrium_trajectory_filenames = [None, None]
            # run a round of equilibrium
            self._equilibrium_results = self._map(feptasks.run_equilibrium, self._equilibrium_results,
                                                  self._hybrid_thermodynamic_states.values(), nsteps_equil,
                                                  hybrid_topology_list, n_eq_iterations_per_call_list,
                                                  atom_indices_to_save_list, equilibrium_trajectory_filenames, eq_splitting, timestep)

    def _adjust_for_correlation(self, timeseries_array: np.array):
        """
        Compute statistical inefficiency for timeseries, returning the timeseries with burn in as well as
        the statistical inefficience and the max number of effective samples

        Parameters
        ----------
        timeseries_array : np.array
            Array of timeseries values

        Returns
        -------
        burned_in_series : np.array
            Array starting after burn in
        statistical_inefficiency : float
            Statistical inefficience of timeseries
        Neff_max : float
            Max number of uncorrelated samples
        """
        [t0, g, Neff_max] = pymbar.timeseries.detectEquilibration(timeseries_array)

        return timeseries_array[t0:], g, Neff_max

    def _endpoint_perturbations(self):
        """
        Compute the correlation-adjusted free energy at the endpoints to the nonalchemical systems.

        Returns
        -------
        df0, ddf0 : list of float
            endpoint pertubation with error for lambda 0, kT
        df1, ddf1 : list of float
            endpoint perturbation for lambda 1, kT
        """
        free_energies = []
        for lambda_endpoint in [0, 1]:
            work_array = np.array(self._reduced_potential_differences[lambda_endpoint])
            burned_in, statistical_inefficiency, Neff_max = self._adjust_for_correlation(work_array)

            _logger.info(
                "Number of effective samples of endpoint pertubation at lambda %d is %f" % (lambda_endpoint, Neff_max))

            df, ddf_raw = pymbar.EXP(burned_in)

            # correct by multiplying the stddev by the statistical inefficiency
            ddf_corrected = ddf_raw * np.sqrt(statistical_inefficiency)

            free_energies.append([df, ddf_corrected])

        return free_energies[0], free_energies[1]

    def _alchemical_free_energy(self):
        """
        Use BAR to compute the free energy between lambda 0 and lambda1

        Returns
        -------
        df : float
            Free energy, kT
        ddf_corrected : float
            Error in free energy, kT
        """
        statistical_inefficiencies = []
        work_arrays = []
        for lambda_endpoint in [0, 1]:
            work_array = np.array(self._total_work[lambda_endpoint])
            work_arrays.append(work_array)

            burned_in, statistical_inefficiency, Neff_max = self._adjust_for_correlation(work_array)

            _logger.info("Number of effective samples of switching at lambda %d is %f" % (lambda_endpoint, Neff_max))

            statistical_inefficiencies.append(statistical_inefficiency)

        # for now we'll take the max of the two to decide how to report the error
        statistical_inefficiency = max(statistical_inefficiencies)

        df, ddf_raw = pymbar.BAR(work_arrays[0], work_arrays[1])

        ddf_corrected = ddf_raw * np.sqrt(statistical_inefficiency)

        return df, ddf_corrected

    @property
    def current_free_energy_estimate(self):
        """
        Estimate the free energy based on currently available values
        """
        # Make sure the task queue is empty (all pending calcs are complete) before computing free energy
        # Make sure the task queue is empty (all pending calcs are complete) before computing free energy
        [[df0, ddf0], [df1, ddf1]] = self._endpoint_perturbations()
        [df, ddf] = self._alchemical_free_energy()

        ddf_overall = np.sqrt(ddf0 ** 2 + ddf1 ** 2 + ddf ** 2)
        return -df0 + df + df1, ddf_overall
