from __future__ import print_function
import numpy as np
import copy
import logging
import traceback
from openmmtools.alchemy import AlchemicalState

class RelativeAlchemicalState(AlchemicalState):
    """
    Relative AlchemicalState to handle all lambda parameters required for relative perturbations

    lambda = 1 refers to ON, i.e. fully interacting while
    lambda = 0 refers to OFF, i.e. non-interacting with the system

    all lambda functions will follow from 0 -> 1 following the master lambda

    lambda*core parameters perturb linearly
    lambda_sterics_insert and lambda_electrostatics_delete perturb in the first half of the protocol 0 -> 0.5
    lambda_sterics_delete and lambda_electrostatics_insert perturb in the second half of the protocol 0.5 -> 1

    Attributes
    ----------
    lambda_sterics_core
    lambda_electrostatics_core
    lambda_sterics_insert
    lambda_sterics_delete
    lambda_electrostatics_insert
    lambda_electrostatics_delete
    """

    # lambda components for each component, all run from 0 -> 1 following master lambda
    lambda_functions = {
        'lambda_sterics_core': lambda x: x,
        'lambda_electrostatics_core': lambda x: x,
        'lambda_sterics_insert': lambda x: 2.0*x if x< 0.5 else 1.0,
        'lambda_sterics_delete': lambda x: 0.0 if x < 0.5 else 2.0*(x-0.5),
        'lambda_electrostatics_insert': lambda x:0.0 if x < 0.5 else 2.0*(x-0.5),
        'lambda_electrostatics_delete': lambda x: 2.0*x if x< 0.5 else 1.0,
        'lambda_bonds': lambda x: x,
        'lambda_angles': lambda x: x,
        'lambda_torsions': lambda x: x
    }

    class _LambdaParameter(AlchemicalState._LambdaParameter):
        pass

    lambda_sterics_core = _LambdaParameter('lambda_sterics_core')
    lambda_electrostatics_core = _LambdaParameter('lambda_electrostatics_core')
    lambda_sterics_insert = _LambdaParameter('lambda_sterics_insert')
    lambda_sterics_delete = _LambdaParameter('lambda_sterics_delete')
    lambda_electrostatics_insert = _LambdaParameter('lambda_electrostatics_insert')
    lambda_electrostatics_delete = _LambdaParameter('lambda_electrostatics_delete')
    lambda_bonds = _LambdaParameter('lambda_bonds')
    lambda_angles = _LambdaParameter('lambda_angles')
    lambda_torsions = _LambdaParameter('lambda_torsions')

    def set_alchemical_parameters(self, master_lambda):
       """Set each lambda value according to the lambda_functions protocol.
       The undefined parameters (i.e. those being set to None) remain
       undefined.
       Parameters
       ----------
       lambda_value : float
           The new value for all defined parameters.
       """
       for parameter_name in self.lambda_functions:
           lambda_value = self.lambda_functions[parameter_name](master_lambda)
           setattr(self, parameter_name, lambda_value)
