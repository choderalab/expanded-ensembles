"""
Test util functions

"""

__author__ = 'John D. Chodera'

import os
from perses.utils.openeye import smiles_to_oemol
from unittest import skipIf

running_on_github_actions = os.environ.get('GITHUB_ACTIONS', None) == 'true'


# functions testing perses.utils.data
@skipIf(running_on_github_actions, "Skip: running on GH Actions")
def test_get_data_filename(datafile='data/gaff2.xml'):
    """
    Checks that function returns real path

    Parameters
    ----------
    datafile : str, default 'data/gaff2.xml'

    """
    from perses.utils.data import get_data_filename
    import os

    path = get_data_filename(datafile)

    assert os.path.exists(path), "Either path to datafile is broken, or datafile does not exist"



# functions testing perses.utils.openeye
@skipIf(running_on_github_actions, "Skip: running on GH Actions")
def test_extractPositionsFromOEMol(molecule=smiles_to_oemol('CC')):
    """
    Generates an ethane OEMol from string and checks it returns positions of correct length and units

    Paramters
    ----------
    smiles : str, default 'CC'
        default is ethane molecule

    Returns
    -------
    positions : np.array
        openmm positions of molecule with units
    """
    from perses.utils.openeye import extractPositionsFromOEMol
    import simtk.unit as unit

    positions = extractPositionsFromOEMol(molecule)

    assert (len(positions) == molecule.NumAtoms()), "Positions extracted from OEMol does not match number of atoms"
    assert (positions.unit == unit.angstrom), "Positions returned are not in expected units of angstrom"

    return positions

@skipIf(running_on_github_actions, "Skip: running on GH Actions")
def test_giveOpenmmPositionsToOEMol(positions=None, molecule=smiles_to_oemol('CC')):
    """
    Checks that positions of an OEMol can be updated using openmm positions by shifting a molecule by 1 A

    Paramters
    ----------
    positions : openmm positions, default None
        openmm positions that will be used to update the OEMol
    molecule : openeye.oechem.OEMol
        OEMol object to update

    Returns
    -------
    updated_molecule : openeye.oechem.OEMol
        OEMol object with updated positions

    """
    from perses.utils.openeye import giveOpenmmPositionsToOEMol
    import simtk.unit as unit
    import copy

    if positions is None:
        positions = test_extractPositionsFromOEMol(molecule)
        update_positions = copy.deepcopy(positions)
        update_positions[0] += 1.*unit.angstrom
    else:
        update_positions = positions

    updated_molecule = copy.deepcopy(molecule)
    updated_molecule = giveOpenmmPositionsToOEMol(update_positions,updated_molecule)

    assert (molecule.GetCoords()[0] != updated_molecule.GetCoords()[0]), "Positions have not been updated successfully"
    new_positions = test_extractPositionsFromOEMol(updated_molecule)
    assert (new_positions.unit == unit.angstrom), "Positions returned are not in expected units of angstrom"

    return updated_molecule

@skipIf(running_on_github_actions, "Skip full test on GH Actions.")
def test_OEMol_to_omm_ff(molecule=smiles_to_oemol('CC')):
    """
    Generating openmm objects for simulation from an OEMol object

    Parameters
    ----------
    molecule : openeye.oechem.OEMol

    Returns
    -------
    system : openmm.System
        openmm system object
    positions : unit.quantity
        positions of the system
    topology : app.topology.Topology
        openmm compatible topology object
    """
    import simtk.openmm.app as app
    import simtk.unit as unit
    from perses.utils.openeye import OEMol_to_omm_ff
    from simtk import openmm
    from openmmforcefields.generators import SystemGenerator
    from openforcefield.topology import Molecule

    #default arguments for SystemGenerators
    barostat = None
    forcefield_files = ['amber14/protein.ff14SB.xml', 'amber14/tip3p.xml']
    forcefield_kwargs = {'removeCMMotion': False, 'ewaldErrorTolerance': 1e-4, 'nonbondedMethod': app.NoCutoff, 'constraints' : app.HBonds, 'hydrogenMass' : 4 * unit.amus}
    small_molecule_forcefield = 'gaff-2.11'
    system_generator = SystemGenerator(forcefields = forcefield_files, barostat=barostat, forcefield_kwargs=forcefield_kwargs,
                                         small_molecule_forcefield = small_molecule_forcefield, molecules=[Molecule.from_openeye(molecule)], cache=None)

    system, positions, topology = OEMol_to_omm_ff(molecule, system_generator)

    assert (type(system) == type(openmm.System())), "An openmm.System has not been generated from OEMol_to_omm_ff()"

    return system, positions, topology


@skipIf(running_on_github_actions, "Skip full test on GH Actions.")
def run_oemol_test_suite(iupac='ethane'):
   """
   Runs all of the oemol related tests for perses.utils.openeye

   Parameters
   ---------
   iupac : str, default 'ethane'

   """
   from openmoltools.openeye import iupac_to_oemol
   import copy
   import numpy as np
   import simtk.unit as unit
   from openeye import oechem

   oemol = iupac_to_oemol(iupac)
   positions = test_extractPositionsFromOEMol(oemol)

   # shifting all of the positions by 1. A
   new_positions = np.zeros(np.shape(positions))
   for atom in range(oemol.NumAtoms()):
       new_positions[atom] = copy.deepcopy(positions[atom]) + [1., 1., 1.]*unit.angstrom
   new_positions *= unit.angstrom

   molecule = test_giveOpenmmPositionsToOEMol(new_positions,oemol)

   smiles = oechem.OECreateSmiString(molecule,oechem.OESMILESFlag_DEFAULT | oechem.OESMILESFlag_Hydrogens)

   smiles_oemol = smiles_to_oemol(smiles)

   # check that the two systems have the same numbers of atoms
   assert (oemol.NumAtoms() == smiles_oemol.NumAtoms()), "Discrepancy between molecule generated from IUPAC and SMILES"


def test_generate_expression():
    from perses.utils.openeye import generate_expression
    list_to_check = ['Hybridization', 'IntType']
    value = generate_expression(list_to_check)
    assert value == 134217984, 'generate_expression didn\'t return expected value'
    # TODO write test for failures too
