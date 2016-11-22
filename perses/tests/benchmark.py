from simtk import openmm, unit
from simtk.openmm import app
import os, os.path
import sys, math
from unittest import skipIf
import numpy as np
from functools import partial
from pkg_resources import resource_filename
from openeye import oechem
if sys.version_info >= (3, 0):
    from io import StringIO
    from subprocess import getstatusoutput
else:
    from cStringIO import StringIO
    from commands import getstatusoutput
import matplotlib as mpl
mpl.use('Agg')
import seaborn as sns

import matplotlib.pyplot as plt

################################################################################
# NUMBER OF ATTEMPTS
################################################################################
niterations = 50
ENV = 'vacuum'
################################################################################
# CONSTANTS
################################################################################

kB = unit.BOLTZMANN_CONSTANT_kB * unit.AVOGADRO_CONSTANT_NA
temperature = 300.0 * unit.kelvin
kT = kB * temperature
beta = 1.0/kT

functions_hybrid = {
    'lambda_sterics' : 'lambda',
    'lambda_electrostatics' : 'lambda',
    'lambda_bonds' : '1.0',
    'lambda_angles' : '0.1*lambda+0.9',
    'lambda_torsions' : '0.7*lambda+0.3'
}
functions_twostage = {
    'lambda_sterics' : '(2*lambda)^4 * step(0.5 - lambda) + (1.0 - step(0.5 - lambda))',
    'lambda_electrostatics' : '2*(lambda - 0.5) * step(lambda - 0.5)',
    'lambda_bonds' : '1.0', # don't soften bonds
    'lambda_angles' : '0.1*lambda+0.9',
    'lambda_torsions' : '0.7*lambda+0.3'
}

def plot_logPs(logps, molecule_name, scheme, component):
    """
    Create line plot of mean and standard deviation of given logPs.

    Arguments:
    ----------
        logps: dict { int : np.ndarray }
            key : number of total NCMC steps
            value : array of `niterations` logP values
        molecule_name : str
            The molecule featured in the NullTestSystem being analyzed
            in ['naphthalene','butane','propane']
        scheme : str
            Which NCMC scheme is being used
            in ['hybrid','two-stage']
        component : str
            Which logP is being plotted
            in ['NCMC','EXEN']
    """
    x = logps.keys()
    x.sort()
    y = [logps[steps].mean() for steps in x]
    dy = [logps[steps].std() for steps in x]
    plt.fill_between(x, [mean - dev for mean, dev in zip(y, dy)], [mean + dev for mean, dev in zip(y, dy)])
    plt.plot(x, y, 'k')
    plt.xscale('log')

    plt.title("Log acceptance probability of {0} ExpandedEnsemble for {1}".format(scheme, molecule_name))
    plt.ylabel('logP')
    plt.xlabel('ncmc steps')
    plt.savefig('{0}_{1}_{2}{3}_logP'.format(ENV, molecule_name, scheme, component))
    print('Saved plot to {0}_{1}_{2}{3}_logP.png'.format(ENV, molecule_name, scheme, component))
    plt.clf()

def benchmark_exen_ncmc_protocol(analyses, molecule_name, scheme):
    """
    For each combination of system and scheme, results are analyzed for
    the following:
    * Over the whole range of total steps:
        * Plot mean and standard deviation of NCMC logP as a function of
          total steps
        * Plot mean and standard deviation of EXEN logP as a function of
          total steps

    Arguments:
    ----------
        analyses : dict { int : perses.Analysis }
            key : number of total NCMC steps
            value : analysis object contained stored information
        molecule_name : str
            The molecule featured in the NullTestSystem being analyzed
            in ['naphthalene','butane','propane']
        scheme : str
            Which NCMC scheme is being used
            in ['hybrid','two-stage']

    Creates 2 plots every time it is called
    """
    components = {
        'logp_accept' : 'EXEN',
        'logp_ncmc' : 'NCMC',
    }

    for component in components.keys():
        print('Finding {0} over nsteps for {1} with {2} NCMC'.format(component, molecule_name, scheme))
        logps = dict()
        for nsteps, analysis in analyses.items():
            ee_sam = analysis._ncfile.groups['ExpandedEnsembleSampler']
            niterations = ee_sam.variables[component].shape[0]
            logps[nsteps] = np.zeros(niterations, np.float64)
            for n in range(niterations):
                logps[nsteps][n] = ee_sam.variables[component][n]
        plot_logPs(logps, molecule_name, scheme, components[component])

def benchmark_ncmc_work_during_protocol():
    """
    Run 50 iterations of ExpandedEnsembleSampler for NullTestSystems
    over a range of total NCMC steps [0, 1, 10, 100, 1000, 10000].

    Benchmark is repeated for Naphthalene, Butane, and Propane test
    systems, using two-stage and hybrid NCMC.

    For each combination of system and scheme, results are analyzed for
    the following:
    * For a given total number of steps:
        * For NCMC steps 100 and above, plot work done by ncmc integrator
          over the course of the protocol
        * Plot histograms of the contributions of each component to the
          overall log acceptance probability
    * Over the whole range of total steps:
        * Plot mean and standard deviation of NCMC logP as a function of
          total steps
        * Plot mean and standard deviation of EXEN logP as a function of
          total steps
    """
    from perses.tests.testsystems import NaphthaleneTestSystem, ButaneTestSystem, PropaneTestSystem
    from perses.analysis import Analysis
    import netCDF4 as netcdf
    import pickle
    import codecs
    molecule_names = {
        'naphthalene' : NaphthaleneTestSystem,
        'butane' : ButaneTestSystem,
        'propane' : PropaneTestSystem,
    }
    methods = {
        'hybrid' : ['geometry-ncmc-geometry', functions_hybrid],
        'two-stage' : ['ncmc-geometry-ncmc', functions_twostage],
    }

    for molecule_name, NullProposal in molecule_names.items():
        print('\nNow testing {0} null transformations'.format(molecule_name))
        for name, [scheme, functions] in methods.items():
            analyses = dict()
            for ncmc_nsteps in [0, 1, 10, 100, 1000, 10000]:
                print('Running {0} {2} ExpandedEnsemble steps for {1} iterations'.format(ncmc_nsteps, niterations, name))
                testsystem = NullProposal(storage_filename='{0}_{1}-{2}steps.nc'.format(molecule_name, name, ncmc_nsteps), scheme=scheme, options={'functions' : functions, 'nsteps' : ncmc_nsteps})
                testsystem.exen_samplers[ENV].verbose = False
                testsystem.exen_samplers[ENV].sampler.verbose = False
                if name == 'hybrid':
                    testsystem.exen_samplers[ENV].ncmc_engine.softening = 0.0
                testsystem.exen_samplers[ENV].run(niterations=niterations)

                analysis = Analysis(testsystem.storage_filename)
                print(analysis.get_environments())
                if ncmc_nsteps > 99:
                    analysis.plot_ncmc_work('{0}_{1}-ncmc_work_over_{2}_steps.pdf'.format(molecule_name, name, ncmc_nsteps))
                analysis.plot_exen_logp_components()
                analyses[ncmc_nsteps] = analysis
            benchmark_exen_ncmc_protocol(analyses, molecule_name, name)


if __name__ == "__main__":
    benchmark_ncmc_work_during_protocol()

