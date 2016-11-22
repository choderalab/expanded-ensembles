"""
Analysis tools for perses automated molecular design.

TODO
----
* Analyze all but last iteration to ensure we can analyze a running simulation?

"""

__author__ = 'John D. Chodera'

################################################################################
# IMPORTS
################################################################################

import os, os.path
import sys, math
import numpy as np
import copy
import time
import netCDF4 as netcdf
import pickle
import json

import matplotlib as mpl
mpl.use('Agg')
import seaborn as sns

from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.pyplot as plt

################################################################################
# LOGGER
################################################################################

import logging
logger = logging.getLogger(__name__)

################################################################################
# ANALYSIS
################################################################################

class Analysis(object):
    """Analysis tools for perses automated design.

    """
    def __init__(self, storage_filename):
        """Open a storage file for analysis.

        """
        # TODO: Replace this with calls to storage API
        self._ncfile = netcdf.Dataset(storage_filename, 'r')
        self.storage_filename = storage_filename

    def get_environments(self):
        """Return a list of environments in storage file.

        Returns
        -------
        environments : list of str
           List of environment names in storage (e.g. []'explicit-complex', 'explicit-ligand'])

        """
        environments = list()
        for group in self._ncfile.groups:
            environments.append( str(group) )
        return environments

    def write_trajectory(self, environmnent, pdb_filename):
        """Write the trajectory of sampled configurations and chemical states.

        Returns
        -------
        environment : str
           Environment name to write trajectory for
        pdbfile : str
           Name of PDB file to generate.

        """
        # TODO
        pass

    def plot_exen_logp_components(self, filename_prefix=None):
        """
        Generate histograms of each component of Expanded Ensemble log acceptance probability
        Components may include:
            logp_topology_proposal
            logp_geometry
                logp_geometry_propose
                logp_geometry_reverse
            logp_switch                       (not present in 'geometry-ncmc-geometry' scheme)
            logp_ncmc_elimination             ('ncmc-geometry-ncmc' scheme only)
            logp_ncmc_introduction            (not present in 'geometry-ncmc-geometry' scheme)
            logp_ncmc                         ('geometry-ncmc-geometry' scheme only)
            new_log_weight
            old_log_weight

        Arguments:
        ----------
        filename_prefix : str, OPTIONAL, default = None
            if specified, each plot is saved as '{0}-{1}'.format(filename_prefix, component)
        Each histogram will be saved to {component name}.png
        TODO: include input filename
            storage ncfile has different hierarchy depending on which samplers are defined;
            this probably only works without SAMS sampling (otherwise top level groups are
            environments)

        """
        components = [
            'logp_topology_proposal',
            'logp_geometry',
            'logp_geometry_propose',
            'logp_geometry_reverse',
            'logp_switch', 
            'logp_ncmc_elimination',
            'logp_ncmc_introduction',
            'logp_ncmc',
            'new_log_weight',
            'old_log_weight',
        ]

        ee_sam = self._ncfile.groups['ExpandedEnsembleSampler']
        if filename_prefix is None:
            filename_prefix = self.storage_filename.split('.')[0]
        filename = '{0}-logP-components.pdf'.format(filename_prefix)
        with PdfPages(filename) as pdf:
            logps = dict()
            for component in components:
                try:
                    niterations = ee_sam.variables[component].shape[0]
                except:
                    continue
                logps[component] = np.zeros(niterations, np.float64)
                for n in range(niterations):
                    logps[component][n] = ee_sam.variables[component][n]
            plt.figure(figsize=(8,12))
            nrows = len(logps.keys())/2 + len(logps.keys())%2
            ncols = 2
            for spot, component in enumerate(logps.keys()):
                row = spot/2
                col = spot%2
                plt.subplot2grid((nrows,ncols),(row,col))
                plt.hist(logps[component])
                plt.title(component)
                #plt.xlabel(component)
            pdf.savefig()
            plt.close()


    def plot_ncmc_work(self, filename):
        """Generate plots of NCMC work.

        Parameters
        ----------
        filename : str
            File to write PDF of NCMC work plots to.

        """
        with PdfPages(filename) as pdf:
            for envname in ['NCMCEngine', 'NCMCHybridEngine']: #self.get_environments():
                modname = envname
                work = dict()
                for direction in ['delete', 'insert']:
                    varname = '/' + modname + '/' + 'work_' + direction
                    try:
                        # TODO: For now, we analyze all but the last sample, so that this can be run on active simulations.
                        # Later, we should find some way to omit the last sample only if it is nonsensical.
                        work[direction] = self._ncfile[varname][:-1,:]
                        print('Found %s' % varname)
                    except Exception as e:
                        pass

                def plot_work_trajectories(pdf, work, title=""):
                    """Generate figures for the specified switching legs.
                    """
                    plt.figure(figsize=(12, 8))

                    nrows = len(work.keys())
                    ncols = 6
                    workcols = 2
                    for (row, direction) in enumerate(work.keys()):
                        #
                        # Plot work vs step
                        #

                        col = 0
                        plt.subplot2grid((nrows,ncols), (row, col), colspan=(ncols-workcols))

                        # Plot average work distribution in think solid line
                        plt.plot(work[direction].mean(0), 'k-', linewidth=1.0, alpha=1.0)
                        # Plot bundle of work trajectories in transparent lines
                        plt.plot(work[direction].T, 'k-', linewidth=0.5, alpha=0.3)
                        # Adjust axes to eliminate large-magnitude outliers (keep 98% of data in-range)
                        workvals = np.ravel(np.abs(work[direction]))
                        worklim = np.percentile(workvals, 98)
                        nsteps = work[direction].shape[1]
                        plt.axis([0, nsteps, -worklim, +worklim])
                        # Label plot
                        if row == 1: plt.xlabel('steps')
                        plt.ylabel('work / kT')
                        plt.title("%s NCMC in environment '%s' : %s" % (title, envname, direction))
                        plt.legend(['average work', 'NCMC attempts'])

                        #
                        # Plot work histogram
                        #

                        col = ncols - workcols
                        plt.subplot2grid((nrows,ncols), (row, col), colspan=workcols)

                        # Plot average work distribution in think solid line
                        #nbins = 40
                        workvals = work[direction][:-1,-1]
                        #plt.hist(workvals, nbins)
                        if workvals.std() != 0.0:
                            sns.distplot(workvals, rug=True)
                        else:
                            print('workvals has stddev of zero')
                            print(workvals)
                        # Adjust axes to eliminate large-magnitude outliers (keep 98% of data in-range)
                        #worklim = np.percentile(workvals, 98)
                        #oldaxis = plt.axis()
                        #plt.axis([-worklim, +worklim, 0, oldaxis[3]])
                        # Label plot
                        if row == 1: plt.xlabel('work / kT')
                        plt.title("total %s work" % direction)

                    pdf.savefig()  # saves the current figure into a pdf page
                    plt.close()

                if len(work) > 0:
                    # Plot work for all chemical transformations.
                    plot_work_trajectories(pdf, work, title='(all transformations)')

                    # Plot work separated out for each chemical transformation
                    #[niterations, nsteps] = work.shape
                    #transformations = dict()
                    #for iteration in range(niterations):
                    #    plot_work_trajectories(pdf, work, title='(all transformations)')
