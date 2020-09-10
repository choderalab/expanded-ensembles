"""
Test storage layer.

TODO:
* Write tests

"""

__author__ = 'John D. Chodera'

################################################################################
# IMPORTS
################################################################################

import os
import os.path
import tempfile
from perses.analysis.analysis import Analysis
from unittest import skipIf

running_on_github_actions = os.environ.get('GITHUB_ACTIONS', None) == 'true'
################################################################################
# TEST ANALYSIS
################################################################################

@skipIf(running_on_github_actions, "Skip analysis test on GH Actions. Currently broken")
def test_analysis():
   """Test analysis tools.
   """
   testsystem_names = ['ValenceSmallMoleculeLibraryTestSystem']

   for testsystem_name in testsystem_names:
       # Create storage.
       tmpfile = tempfile.NamedTemporaryFile()
       storage_filename = tmpfile.name

       import perses.tests.testsystems
       testsystem_class = getattr(perses.tests.testsystems, testsystem_name)

       # Instantiate test system.
       testsystem = testsystem_class(storage_filename=storage_filename)

       # Alter settings
       for environment in testsystem.environments:
           testsystem.mcmc_samplers[environment].verbose = False
           testsystem.mcmc_samplers[environment].nsteps = 5 # use fewer MD steps to speed things up
           testsystem.exen_samplers[environment].verbose = False
            # HBM this line is broken - ExpandedEnsembleSampler doesn't have attribute ncmc_engine
           testsystem.exen_samplers[environment].ncmc_engine.nsteps = 5 # NCMC switching
           testsystem.sams_samplers[environment].verbose = False

       # Run test simulations.
       niterations = 5 # just a few iterations
       if testsystem.designer is not None:
           # Run the designer
           testsystem.designer.verbose = False
           testsystem.designer.run(niterations=niterations)
       else:
           # Run the SAMS samplers.
           for environment in testsystem.environments:
               testsystem.sams_samplers[environment].run(niterations=niterations)

       # Analyze file.
       # TODO: Use temporary filenames
       analysis = Analysis(storage_filename)
       analysis.plot_ncmc_work('ncmc.pdf')

if __name__ == '__main__':
    #analysis = Analysis('output-10000.nc')
    #analysis.plot_ncmc_work('ncmc-10000.pdf')
    test_analysis()
