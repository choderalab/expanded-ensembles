"""
Test storage layer.

"""

__author__ = 'John D. Chodera'

################################################################################
# IMPORTS
################################################################################

import os
import os.path
import tempfile
from functools import partial

from perses.storage import NetCDFStorage, NetCDFStorageView

from unittest import skipIf
running_on_github_actions = os.environ.get('GITHUB_ACTIONS', None) == 'true'

################################################################################
# TEST STORAGE
################################################################################

def test_storage_create():
    """Test storage layer creating a new file.
    """
    tmpfile = tempfile.NamedTemporaryFile()
    storage = NetCDFStorage(tmpfile.name, mode='w')
    storage.close()

def test_storage_append():
    """Test storage layer appending to a file.
    """
    tmpfile = tempfile.NamedTemporaryFile()
    storage = NetCDFStorage(tmpfile.name, mode='w')
    storage.close()
    storage = NetCDFStorage(tmpfile.name, mode='a')
    storage.close()

def test_sync():
    """Test writing of a quantity.
    """
    tmpfile = tempfile.NamedTemporaryFile()
    storage = NetCDFStorage(tmpfile.name, mode='w')
    storage.sync()

def test_storage_view():
    """Test writing of a quantity.
    """
    tmpfile = tempfile.NamedTemporaryFile()
    storage = NetCDFStorage(tmpfile.name, mode='w')
    view1 = NetCDFStorageView(storage, envname='envname')
    view2 = NetCDFStorageView(view1, modname='modname')
    assert (view1._envname == 'envname')
    assert (view2._envname == 'envname')
    assert (view2._modname == 'modname')

def test_write_quantity():
    """Test writing of a quantity.
    """
    tmpfile = tempfile.NamedTemporaryFile()
    storage = NetCDFStorage(tmpfile.name, mode='w')
    view = NetCDFStorageView(storage, 'envname', 'modname')

    view.write_quantity('singleton', 1.0)

    for iteration in range(10):
        view.write_quantity('varname', float(iteration), iteration=iteration)

    for iteration in range(10):
        assert (storage._ncfile['/envname/modname/varname'][iteration] == float(iteration))

def test_write_array():
    """Test writing of a array.
    """
    tmpfile = tempfile.NamedTemporaryFile()
    storage = NetCDFStorage(tmpfile.name, mode='w')
    view1 = NetCDFStorageView(storage, 'envname1', 'modname')
    view2 = NetCDFStorageView(storage, 'envname2', 'modname')

    from numpy.random import random
    shape = (10,3)
    array = random(shape)
    view1.write_array('singleton', array)

    for iteration in range(10):
        array = random(shape)
        view1.write_array('varname', array, iteration=iteration)
        view2.write_array('varname', array, iteration=iteration)

    for iteration in range(10):
        array = storage._ncfile['/envname1/modname/varname'][iteration]
        assert array.shape == shape
        array = storage._ncfile['/envname2/modname/varname'][iteration]
        assert array.shape == shape

def test_write_object():
    """Test writing of a object.
    """
    tmpfile = tempfile.NamedTemporaryFile()
    storage = NetCDFStorage(tmpfile.name, mode='w')

    #use names we might encounter in simulation
    envname = 'vacuum'
    modname = 'ExpandedEnsembleSampler'
    varname = 'energy'


    view = NetCDFStorageView(storage, envname, modname)

    obj = { 0 : 0 }
    view.write_object('singleton', obj)

    for iteration in range(10):
        obj = { 'iteration' : iteration }
        view.write_object(varname, obj, iteration=iteration)

    for iteration in range(10):
        obj = storage.get_object(envname, modname, varname, iteration=iteration)
        assert ('iteration' in obj)
        assert (obj['iteration'] == iteration)

def run_sampler(sampler, niterations):
    sampler.run(niterations)

@skipIf(running_on_github_actions, "Skip slow test on GH Actions.")
def test_storage_with_samplers():
    """Test storage layer inside all samplers.
    """
    testsystem_names = ['ValenceSmallMoleculeLibraryTestSystem']
    niterations = 5 # number of iterations to run

    for testsystem_name in testsystem_names:
        # Create storage.
        tmpfile = tempfile.NamedTemporaryFile()
        filename = tmpfile.name

        import perses.tests.testsystems
        testsystem_class = getattr(perses.tests.testsystems, testsystem_name)
        # Instantiate test system.
        testsystem = testsystem_class(storage_filename=filename)

        # Test MCMCSampler samplers.
        for environment in testsystem.environments:
            mcmc_sampler = testsystem.mcmc_samplers[environment]
            mcmc_sampler.verbose = False
            f = partial(run_sampler, mcmc_sampler, niterations)
            f.description = "Testing MCMCSampler for %s with environment %s" % (testsystem_name, environment)
            #yield f
            f()
        # Test ExpandedEnsembleSampler samplers.
        for environment in testsystem.environments:
            exen_sampler = testsystem.exen_samplers[environment]
            exen_sampler.verbose = False
            f = partial(run_sampler, exen_sampler, niterations)
            f.description = "Testing ExpandedEnsembleSampler for %s with environment %s" % (testsystem_name, environment)
            #yield f
            f()
        # Test SAMSSampler samplers.
        for environment in testsystem.environments:
            sams_sampler = testsystem.sams_samplers[environment]
            sams_sampler.verbose = False
            f = partial(run_sampler, sams_sampler, niterations)
            f.description = "Testing SAMSSampler for %s with environment %s" % (testsystem_name, environment)
            #yield f
            f()
        # Test MultiTargetDesign sampler, if present.
        if hasattr(testsystem, 'designer') and (testsystem.designer is not None):
            testsystem.designer.verbose = False
            f = partial(run_sampler, testsystem.designer, niterations)
            f.description = "Testing designer for %s with environment %s" % (testsystem_name, environment)
            #yield f
            f()

if __name__=="__main__":
    test_write_object()
