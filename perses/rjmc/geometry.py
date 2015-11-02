"""
This contains the base class for the geometry engine, which proposes new positions
for each additional atom that must be added.
"""
from collections import namedtuple
import numpy as np
import scipy.stats as stats
import numexpr as ne
import simtk.unit as units
import logging
import sympy
from sympy.physics import vector


GeometryProposal = namedtuple('GeometryProposal',['new_positions','logp'])


class GeometryEngine(object):
    """
    This is the base class for the geometry engine.
    
    Arguments
    ---------
    metadata : dict
        GeometryEngine-related metadata as a dict
    """
    
    def __init__(self, metadata):
        pass

    def propose(self, new_to_old_atom_map, new_system, old_system, old_positions):
        """
        Make a geometry proposal for the appropriate atoms.
        
        Arguments
        ----------
        new_to_old_atom_map : dict
            mapping of the new to old atoms
        new_system : simtk.openmm.System
            The new system object
        old_system : simtk.openmm.System
            The old system object
        old_positions : [m, 3] np.array of floats
            The positions of the old m atoms

        Returns
        -------
        proposal : GeometryProposal namedtuple
             Contains the new positions as well as the logp ratio
             of the proposal.
        """
        return GeometryProposal(np.array([0.0,0.0,0.0]), 0)


class FFGeometryEngine(GeometryEngine):
    """
    This is an implementation of the GeometryEngine class which proposes new dimensions based on the forcefield parameters
    """


    def propose(self, new_to_old_atom_map, new_system, old_system, old_positions):
        """
        Propose a new geometry for the appropriate atoms using forcefield parameters

        Arguments
        ----------
        new_to_old_atom_map : dict
            mapping of the new to old atoms
        new_system : simtk.openmm.System
            The new system object
        old_system : simtk.openmm.System
            The old system object
        old_positions : [m, 3] np.array of floats
            The positions of the old m atoms

        Returns
        -------
        new_positions : [m, 3] np.array of floats
            The positions of the m atoms in the new system (if not propose_positions, this is the old positions)
        logp : float
            The logp of the proposal, including the jacobian
        """

        #get the mapping between new and old atoms
        n_atoms_new = new_system.getNumParticles()
        n_atoms_old = old_system.getNumParticles()

        #get a list of the new atoms
        new_atoms = [atom for atom in range(n_atoms_new) if atom not in new_to_old_atom_map.keys()]

        #get a list of the old atoms
        old_atoms = [atom for atom in range(n_atoms_old) if atom not in new_to_old_atom_map.values()]

        #create a new array with the appropriate size
        new_positions = np.zeros([n_atoms_new, 3])

        #transfer known positions--be careful about units!
        for atom in new_to_old_atom_map.keys():
            new_positions[atom] = old_positions[new_to_old_atom_map[atom]].in_units_of(units.nanometers)

        #restore units
        new_positions = new_positions*units.nanometers
        #get a new set of positions and a logp of that proposal
        final_new_positions, logp_forward = self._propose_new_positions(new_atoms, new_system, new_positions, new_to_old_atom_map)

        #get the probability of a reverse proposal
        #first get the atom map reversed
        old_to_new_atom_map = {value: key for (key, value) in new_to_old_atom_map.items()}
        _, logp_reverse = self._propose_new_positions(old_atoms, old_system, old_positions, old_to_new_atom_map, propose_positions=False)

        #construct the return object
        geometry_proposal = GeometryProposal(final_new_positions, logp_reverse - logp_forward)

        return geometry_proposal

    def _propose_new_positions(self, new_atoms, new_system, new_positions, new_to_old_atom_map, propose_positions=True):
        """
        Propose new atomic positions, or get the probability of existing ones
        (used to evaluate the reverse probability, where the old atoms become the new atoms, etc)
        Arguments
        ---------
        new_atoms : list of int
            Indices of atoms that need positions
        topology_proposal : TopologyProposal namedtuple
            The result of the topology proposal, containing the atom mapping and topologies
        new_system : simtk.OpenMM.System object
            The new system
        new_positions : [m, 3] np.array of floats
            The positions of the m atoms in the new system, with unpositioned atoms having [0,0,0]

        Returns
        -------
        new_positions : [m, 3] np.array of floats
            The positions of the m atoms in the new system (if not propose_positions, this is the old positions)
        logp : float
            The logp of the proposal, including the jacobian
        """
        logp = 0.0
        atoms_with_positions = new_to_old_atom_map.keys()
        forces = {new_system.getForce(index).__class__.__name__ : new_system.getForce(index) for index in range(new_system.getNumForces())}
        torsion_force = forces['PeriodicTorsionForce']
        bond_force = forces['HarmonicBondForce']
        atomic_proposal_order = self._generate_proposal_order(new_atoms, new_system, new_to_old_atom_map)
        for atomset in atomic_proposal_order:
            for atom, possible_torsions in atomset.items():
                #randomly select a torsion from the available list, and calculate its log-probability
                selected_torsion_index = np.random.randint(0, len(possible_torsions))
                selected_torsion = possible_torsions[selected_torsion_index]
                logp_selected_torsion = - np.log(len(possible_torsions))
                torsion_parameters = torsion_force.getTorsionParameters(selected_torsion)
                #check to see whether the atom is the first or last in the torsion
                if atom == torsion_parameters[0]:
                    bonded_atom = torsion_parameters[1]
                    angle_atom = torsion_parameters[2]
                    torsion_atom = torsion_parameters[3]
                else:
                    bonded_atom = torsion_parameters[2]
                    angle_atom = torsion_parameters[1]
                    torsion_atom = torsion_parameters[0]

                #get a new position for the atom, if specified
                if propose_positions:
                    atomic_xyz, logp_atomic = self._propose_atomic_position(new_system, atom, bonded_atom, angle_atom, torsion_atom, torsion_parameters, new_positions)
                    new_positions[atom] = atomic_xyz
                else:
                    logp_atomic = self._calculate_logp_atomic_position(new_system, new_positions, atom, bonded_atom, angle_atom, torsion_parameters)
                logp += logp_atomic + logp_selected_torsion

        return new_positions, logp

    def _generate_proposal_order(self, new_atoms, new_system, new_to_old_atom_map):
        """
        Generate the list of atoms (and corresponding torsions) to be proposed each round

        Arguments
        ---------
        new_atoms : list of int
            Indices of atoms that need positions
        topology_proposal : TopologyProposal namedtuple
            The result of the topology proposal, containing the atom mapping and topologies
        new_system : simtk.OpenMM.System object
            The new system
        new_positions : [m, 3] np.array of floats
            The positions of the m atoms in the new system, with unpositioned atoms having [0,0,0]

        Returns
        -------
        atom_proposal_list : list of dict
            List of dict of form {atom_index : [torsion_list]}. each entry in the list is a
            round of atoms eligible for proposal.
        """
        atoms_with_positions = new_to_old_atom_map.keys()
        forces = {new_system.getForce(index).__class__.__name__ : new_system.getForce(index) for index in range(new_system.getNumForces())}
        torsion_force = forces['PeriodicTorsionForce']
        bond_force = forces['HarmonicBondForce']
        atom_proposal_list = []
        while len(new_atoms) > 0:
            atom_torsion_proposals = self._atoms_eligible_for_proposal(torsion_force, bond_force, atoms_with_positions, new_atoms)
            #now loop through the list of atoms found to be eligible for positions this round
            atom_proposal_list.append(atom_torsion_proposals)
            for atom in atom_torsion_proposals.keys():
                #remove atom from list of atoms needing positions
                new_atoms.remove(atom)
                atoms_with_positions.append(atom)
        return atom_proposal_list



    def _propose_atomic_position(self, system, atom, bonded_atom, angle_atom, torsion_atom, torsion_parameters, positions):
        """
        Method to propose the position of a single atom.

        Arguments
        ---------
        system : simtk.openmm.System
            the system containing the atom of interest
        atom : int
            the index of the atom whose position should be proposed
        bonded_atom : int
            the index of the atom bonded to the atom of interest
        angle_atom : int
            the 1, 3 position atom index
        torsion_parameters : dict
            The parameters of the torsion that is being used for this proposal

        Returns
        -------
        atomic_xyz : [1,3] np.array of floats
            the cartesian coordinates of the atom
        logp_atomic_position : float
            the logp of this proposal, including jacobian correction
        """
        logging.debug("Proposing position %d with bond atom %d, angle atom %d, torsion atom %d" % (atom, bonded_atom, angle_atom, torsion_atom))
        #get bond parameters and draw bond length
        r0, k_eq = self._get_bond_parameters(system, atom, bonded_atom)
        r, logp_bond = self._propose_bond_length(r0, k_eq)

        #get angle parameters and draw bond angle
        theta0, k_eq = self._get_angle_parameters(system, atom, bonded_atom, angle_atom)
        theta, logp_angle = self._propose_bond_angle(theta0, k_eq)

        #propose torsion
        phi, logp_torsion = self._propose_torsion_angle(torsion_parameters[6], torsion_parameters[4], torsion_parameters[5])
        #convert spherical to cartesian coordinates
        spherical_coordinates = np.asarray([r, theta, phi])
        atomic_xyz, detJ = self._internal_to_cartesian(bonded_atom, angle_atom, torsion_atom, r, theta, phi, positions)
        #accumulate the forward logp with jacobian correction
        logp_atomic_position = (logp_angle + logp_bond + logp_torsion + np.log(detJ))
        logging.debug("Proposed (r, theta, phi) of (%s, %s, %s)" % (str(r), str(theta), str(phi)))
        return atomic_xyz, logp_atomic_position


    def _calculate_logp_atomic_position(self, system, positions, atom, bonded_atom, angle_atom, torsion_parameters):
        """
        Calculate the log-probability of a given atom's position

        Arguments
        ---------

        Returns
        -------
        logp : float
            logp of position, including det(J) correction
        """
        #convert cartesian coordinates to spherical
        relative_xyz = positions[atom] - positions[bonded_atom]
        spherical_coordinates, detJ = self._cartesian_to_spherical(relative_xyz)

        #get bond parameters and calculate the probability of choosing the current positions
        r0, k_eq = self._get_bond_parameters(system, atom, bonded_atom)
        sigma_bond = 1.0/np.sqrt(2.0*k_eq/k_eq.unit)
        logp_bond = stats.distributions.norm.logpdf(spherical_coordinates[0]/spherical_coordinates[0].unit, r0/r0.unit, sigma_bond)

        #calculate the probability of choosing this bond angle
        theta0, k_eq = self._get_angle_parameters(system, atom, bonded_atom, angle_atom)
        sigma_angle = 1.0/np.sqrt(2.0*k_eq/k_eq.unit)
        logp_angle = stats.distributions.norm.logpdf(spherical_coordinates[1]/spherical_coordinates[1].unit, theta0/theta0.unit, sigma_angle)

        #calculate the probabilty of choosing this torsion angle
        torsion_Z, _ = self._torsion_normalizer(torsion_parameters[6], torsion_parameters[4], torsion_parameters[5])
        logp_torsion = self._torsion_p(torsion_Z,torsion_parameters[6], torsion_parameters[4], torsion_parameters[5], spherical_coordinates[2]/spherical_coordinates[2].unit)

        logp_atomic_position = (logp_angle + logp_bond + logp_torsion + np.log(detJ))

        return logp_atomic_position

    def _atoms_eligible_for_proposal(self, torsion_force, bond_force, atoms_with_positions, new_atoms):
        """
        Determine which atoms are eligible for position proposal this round.
        The criteria are that the atom must not yet have a position, and must
        be 1 or 4 in at least one torsion where the other atoms have positoins.

        Arguments
        ---------
        torsion_force : simtk.openmm.PeriodicTorsionForce
            The torsion force from the system
        bond_roce : simtk.openmm.HarmonicBondForce
            The HarmonicBondForce from the system
        atoms_with_positions : list of int
            A list of the indices of atoms that have positions

        Returns
        -------
        atom_torsion_proposals : dict
            Dictionary containing {eligible_atom : [list_of_torsions]}

        """
        current_atom_proposals = []
        atom_torsion_proposals = {atom_index: [] for atom_index in new_atoms}
        for torsion_index in range(torsion_force.getNumTorsions()):
            atom1 = torsion_force.getTorsionParameters(torsion_index)[0]
            atom2 = torsion_force.getTorsionParameters(torsion_index)[1]
            atom3 = torsion_force.getTorsionParameters(torsion_index)[2]
            atom4 = torsion_force.getTorsionParameters(torsion_index)[3]
            #Only take torsions where the "new" statuses of atoms 1 and 4 are not equal
            if (atom1 in atoms_with_positions) != (atom4 in atoms_with_positions):
                #make sure torsion is not improper:
                if not self._is_proper(torsion_force.getTorsionParameters(torsion_index), bond_force):
                    continue
                #only take torsions where 2 and 3 have known positions
                if (atom2 in atoms_with_positions) and (atom3 in atoms_with_positions):
                    #finally, append the torsion index to the list of possible atom sets for proposal
                    if atom1 in atoms_with_positions and (atom1 not in current_atom_proposals):
                        atom_torsion_proposals[atom4].append(torsion_index)
                        current_atom_proposals.append(atom4)
                    elif atom4 in atoms_with_positions and (atom4 not in current_atom_proposals):
                        atom_torsion_proposals[atom1].append(torsion_index)
                        current_atom_proposals.append(atom1)
                    else:
                        continue
        return {atom: torsionlist for atom, torsionlist in atom_torsion_proposals.items() if len(torsionlist) > 0}


    def _is_proper(self, torsion_parameters, bond_force):
        """
        Utility function to determine if torsion is proper
        """
        #get the atoms
        torsion_atoms = torsion_parameters[:4]
        is_proper = True
        for i in range(3):
            is_proper = is_proper and self._is_bond(torsion_atoms[i], torsion_atoms[i+1], bond_force)
        return is_proper

    def _is_bond(self, atom1, atom2, bond_force):
        """
        Utility function to determine if bond exists between two atoms
        """
        for bond_index in range(bond_force.getNumBonds()):
            parameters = bond_force.getBondParameters(bond_index)
            if (parameters[0] == atom1 and parameters[1] == atom2) or (parameters[1] == atom1 and parameters[0] == atom2):
                return True
        return False


    def _get_bond_parameters(self, system, atom1, atom2):
        """
        Utility function to get the bonded parameters for a pair of atoms

        Arguments
        ---------
        system : simtk.openmm.System
            the system containing the parameters of interest
        atom1 : int
            the index of the first atom in the bond
        atom2 : int
            the index of the second atom in the bond

        Returns
        -------
        r0 : float
            equilibrium bond length
        k_eq : float
            bond spring constant
        """
        forces = {system.getForce(index).__class__.__name__ : system.getForce(index) for index in range(system.getNumForces())}
        bonded_force = forces['HarmonicBondForce']
        for bond_index in range(bonded_force.getNumBonds()):
            parameters = bonded_force.getBondParameters(bond_index)
            if (parameters[0] == atom1 and parameters[1] == atom2) or (parameters[1] == atom1 and parameters[0] == atom2):
                return parameters[2], parameters[3]

    def _get_angle_parameters(self, system, atom1, atom2, atom3):
        """
        Utility function to retrieve harmonic angle parameters for
        a given set of atoms in a system

        Arguments
        ---------
        system : simtk.openmm.System
            the system with parameters
        atom1 : int
            the index of the first atom
        atom2 : int
            the index of the second atom
        atom3 : int
            the index of the third atom

        Returns
        -------
        theta0 : float
            equilibrium bond angle
        k_eq : float
            angle spring constant
        """
        list_of_angle_atoms = [atom1, atom2, atom3]
        #compare sorted lists of atoms
        forces = {system.getForce(index).__class__.__name__ : system.getForce(index) for index in range(system.getNumForces())}
        angle_force = forces['HarmonicAngleForce']
        for angle_index in range(angle_force.getNumAngles()):
            parameters = angle_force.getAngleParameters(angle_index)
            #the first three "parameters" are atom indices
            atoms = parameters[:3]
            if np.all(np.equal(list_of_angle_atoms, atoms)) or np.all(np.equal(list_of_angle_atoms[::-1], atoms)):
                return parameters[3], parameters[4]

    def _get_unique_atoms(self, new_to_old_map, new_atom_list, old_atom_list):
        """
        Get the set of atoms unique to both the new and old system.

        Arguments
        ---------
        new_to_old_map : dict
            Dictionary of the form {new_atom : old_atom}

        Returns
        -------
        unique_old_atoms : list of int
            A list of the indices of atoms unique to the old system
        unique_new_atoms : list of int
            A list of the indices of atoms unique to the new system
        """
        mapped_new_atoms = new_to_old_map.keys()
        unique_new_atoms = [atom for atom in new_atom_list not in mapped_new_atoms]
        mapped_old_atoms = new_to_old_map.values()
        unique_old_atoms = [atom for atom in old_atom_list not in mapped_old_atoms]
        return unique_old_atoms, unique_new_atoms

    def _propose_bond_length(self, r0, k_eq):
        """
        Draw a bond length from the equilibrium bond length distribution

        Arguments
        ---------
        r0 : float
            The equilibrium bond length
        k_eq : float
            The bond spring constant

        Returns
        --------
        r : float
            the proposed bond length
        logp : float
            the log-probability of the proposal
        """
        sigma = 2.0/np.sqrt(2.0*k_eq/k_eq.unit)
        r = sigma*np.random.random()*r0.unit + r0
        logp = stats.distributions.norm.logpdf(r/r.unit, r0/r0.unit, sigma)
        return (r, logp)

    def _propose_bond_angle(self, theta0, k_eq):
        """
        Draw a bond length from the equilibrium bond length distribution

        Arguments
        ---------
        theta0 : float
            The equilibrium bond angle
        k_eq : float
            The angle spring constant

        Returns
        --------
        r : float
            the proposed bond angle
        logp : float
            the log-probability of the proposal
        """
        sigma = 1.0/np.sqrt(2.0*k_eq/k_eq.unit)
        theta = sigma*np.random.random()*theta0.unit + theta0
        logp = stats.distributions.norm.logpdf(theta / theta.unit, theta0/theta0.unit, sigma)
        return (theta, logp)

    def _propose_torsion_angle(self, V, n, gamma):
        """
        Draws a torsion angle from the distribution of one torsion.
        Uses the functional form U(phi) = V/2[1+cos(n*phi-gamma)],
        as described by Amber.

        Arguments
        ---------
        V : float
            force constant
        n : int
            multiplicity of the torsion
        gamma : float
            phase angle

        Returns
        -------
        phi : float
            The proposed torsion angle
        logp : float
            The proposed log probability of the torsion angle
        """
        (Z, max_p) = self._torsion_normalizer(V, n, gamma)
        phi = 0.0
        logp = 0.0
        #sample from the distribution
        accepted = False
        #use rejection sampling
        while not accepted:
            phi_samp = np.random.uniform(0.0, 2*np.pi)
            runif = np.random.uniform(0.0, max_p+1.0)
            p_phi_samp = self._torsion_p(Z, V, n, gamma, phi_samp)
            if p_phi_samp > runif:
                phi = phi_samp
                logp = np.log(p_phi_samp)
                accepted = True
            else:
                continue
        return (phi*units.radians, logp)

    def _torsion_p(self, Z, V, n, gamma, phi):
        """
        Utility function for calculating the normalized probability of a torsion angle
        """
        #must remove units
        V = V/V.unit
        gamma = gamma/gamma.unit
        return (1.0/Z)*np.exp(-(V/2.0)*(1+np.cos(n*phi-gamma)))

    def _torsion_normalizer(self, V, n, gamma):
        """
        Utility function to numerically normalize torsion angles.
        Also return max_p to facilitate rejection sampling
        """
        #generate a grid of 5000 points from 0 < phi < 2*pi
        phis = np.linspace(0, 2.0*np.pi, 5000)
        #evaluate the unnormalized probability at each of those points
        #need to remove units--numexpr can't handle them otherwise
        V = V/V.unit
        gamma = gamma/gamma.unit
        phi_q = ne.evaluate("exp(-(V/2.0)*(1+cos(n*phis-gamma)))")
        #integrate the values
        Z = np.trapz(phi_q, phis)
        max_p = np.max(phi_q/Z)
        return Z, max_p

    def _internal_to_cartesian(self, bond_atom, angle_atom, torsion_atom, r, theta, phi, positions):
        """
        Convert the internal coordinates to cartesian via sympy, also produce detJ
        """
        N = vector.ReferenceFrame('N')
        bond_position = positions[bond_atom]
        angle_position = positions[angle_atom]
        torsion_position = positions[torsion_atom]
        r_sym, theta_sym, phi_sym = sympy.symbols('r_sym, theta_sym, phi_sym')

        bond_vec = N.x*bond_position[0] + N.y*bond_position[1] + N.z*bond_position[2]
        angle_vec = N.x*angle_position[0] + N.y*angle_position[1] + N.z*angle_position[2]
        torsion_vec = N.x*torsion_position[0] + N.y*torsion_position[1] + N.z*torsion_position[2]

        #2-3 bond
        a = angle_vec - bond_vec
        a_u = a.normalize()

        #3-4 bond
        b = angle_vec - torsion_vec
        b_u = b.normalize()

        d = r_sym*a_u

        normal = vector.cross(a, b)

        #rotate the vector about the normal (bond angle)
        angle_rotation = N.orientnew('angle_rotation', 'Axis',[theta_sym, normal])
        angle_dcm = N.dcm(angle_rotation)
        d_theta = vector.dot(angle_dcm, d)

        #rotate the vector about the 2-3 bond (torsion)
        torsion_rotation = N.orientnew('torsion_rotation', 'Axis', [phi_sym, a])
        torsion_dcm = N.dcm(torsion_rotation)
        d_phi = vector.dot(torsion_dcm, d_theta)

        #lambdify the expression and calculate the new coordinates
        cartesian_atomic_position = sympy.lambdify("r_sym, theta_sym, phi_sym", d_phi, "numpy")(r, theta, phi)

        #now get a jacobian matrix, take the determinant and absolute value, and we're done
        d_phi_matrix = d_phi.to_matrix(N)
        d_x = d_phi_matrix[0]
        d_y = d_phi_matrix[1]
        d_z = d_phi_matrix[2]

        jacobian_r = [sympy.diff(d_x, 'r_sym'), sympy.diff(d_y, 'r_sym'), sympy.diff(d_z, 'r_sym')]
        jacobian_theta = [sympy.diff(d_x, 'theta_sym'), sympy.diff(d_y, 'theta_sym'), sympy.diff(d_z, 'theta_sym')]
        jacobian_phi = [sympy.diff(d_x, 'phi_sym'), sympy.diff(d_y, 'phi_sym'), sympy.diff(d_z, 'phi_sym')]
        jacobian = sympy.Matrix([jacobian_r, jacobian_theta, jacobian_phi])
        jacobian_determinant = jacobian.det(method='berkowitz')

        detJ = sympy.lambdify('r_sym, theta_sym, phi_sym', jacobian_determinant)(r, theta, phi)

        return np.array(cartesian_atomic_position), np.abs(detJ)



    def _cartesian_to_internal(self, atom, bond_atom, angle_atom, torsion_atom, positions):
        """
        Use sympy to derive the r, theta, phi for
        """
        N = vector.ReferenceFrame('N')
        Xa, Ya, Za = sympy.symbols("Xa Ya Za")

        atomic_position = positions[atom]
        bond_position = positions[bond_atom]
        angle_position = positions[angle_atom]
        torsion_position = positions[torsion_atom]
        atom_vec = N.x*Xa+N.y*Ya+N.z*Za
        bond_vec = N.x*bond_position[0] + N.y*bond_position[1] + N.z*bond_position[2]
        angle_vec = N.x*angle_position[0] + N.y*angle_position[1] + N.z*angle_position[2]
        torsion_vec = N.x*torsion_position[0] + N.y*torsion_position[1] + N.z*torsion_position[2]

        a = bond_vec - atom_vec
        b = angle_vec - bond_vec
        c = torsion_vec - angle_vec

        atom_pos_u = atom_vec.normalize()
        a_u = a.normalize()
        b_u = b.normalize()
        c_u = c.normalize()

        #bond length
        r_exp = a.magnitude()

        #bond angle
        theta_exp = sympy.acos(vector.dot(-a_u, b_u))

        #torsion
        plane1 = vector.cross(a, b)
        plane2 = vector.cross(b, c)

        plane1_norm = plane1.magnitude()
        plane2_norm = plane2.magnitude()

        plane_dot_u = vector.dot(plane1, plane2) / (plane1_norm * plane2_norm)

        phi_exp = sympy.acos(plane_dot_u)

        r = sympy.lambdify("Xa, Ya, Za", r_exp, "numpy")(*atomic_position)
        theta = sympy.lambdify("Xa, Ya, Za", theta_exp, "numpy")(*atomic_position)
        phi = sympy.lambdify("Xa, Ya, Za", phi_exp, "numpy")(*atomic_position)

        #calculate the jacobian and its determinant
        j_rows = []
        for sym in [r_exp, theta_exp, phi_exp]:
            j_rows.append([sym.diff(Xa), sym.diff(Ya), sym.diff(Za)])
        jacobian = sympy.Matrix(j_rows)
        jacobian_determinant = jacobian.det(method='berkowitz')

        detJ = sympy.lambdify("Xa, Ya, Za",jacobian_determinant, "numpy")(*atomic_position)


        return np.array([r, theta, phi]), np.abs(detJ)

