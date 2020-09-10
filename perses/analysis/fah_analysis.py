import numpy as np
import pandas as pd
import seaborn as sns
from pymbar import BAR
import matplotlib.pyplot as plt
import seaborn
from simtk.openmm import unit
import json
from tqdm.auto import tqdm
from openmmtools.constants import kB
import joblib
import logging
import os
from typing import Optional
from perses.analysis.resample import bootstrap_uncorrelated


def _strip_outliers(w, max_value=1e4, n_devs=5):
    """Removes work values that are more than 5 (n_devs) standard deviations from the mean or larger than 1E4 (max_value).

    Parameters
    ----------
    w : list(float)
        List of works
    max_value : int, default=1E4
        Work values larger than this will be discarded
    n_devs : int, default=5
        Number of standard deviations from mean to be returned

    Returns
    -------
    list(float)
        Tidied list of work values

    """
    w = w[w.abs() < max_value]
    return w[(w - w.mean()).abs() < n_devs * w.std()]


def _get_works(df, run, project, GEN=None):
    """ Get set of work values from a dataframe

    Parameters
    ----------
    df : pandas.DataFrame
        Information generated from folding at home
    run : str
        run to collect data for (i.e. 'RUN0')
    project : str
        project to collect data for (i.e. 'PROJ13420')
    GEN : str, optional default=None
        if provided, will only return work values from a given run, (i.e. 'GEN0'), otherwise all generations are returned

    Returns
    -------
    list, list
        Returns lists for forwards and backwards works

    """
    works = df[df["RUN"] == run]

    if GEN:
        works = works[works["GEN"] == GEN]

    f = works[works["PROJ"] == project].forward_work
    r = works[works["PROJ"] == project].reverse_work
    return f, r


def free_energies(
    details_file_path: list,
    work_file_path: str,
    complex_project: str,
    solvent_project: str,
    temperature_kelvin: float = 300.0,
    n_bootstrap: int = 100,
    show_plots: bool = False,
    plot_file_format: str = "pdf",
    cache_dir: Optional[str] = None,
    min_num_work_values: int = 10,
):
    r"""Compute free energies from a set of runs.

    Parameters
    ----------
    details_file_path : list
        Path or list of paths to json file containing run metadata.
    work_file_path : str
        Path to bz2-compressed pickle file containing work values from simulation
    complex_project : str
        Project identifier (of the form "PROJXXXXX") of the FAH project for complex simulation
    solvent_project : str
        Project identifier (of the form "PROJXXXXX") of the FAH project for solvent simulation
    temperature_kelvin : float
    n_bootstrap : int, optional
        Number of bootstrap steps used for BAR free energy estimation
    show_plots : bool, optional
        If true, block to display plots interactively (using `plt.show()`).
        If false, save plots as images in current directory (for batch usage).
    plot_file_format : str, optional
        Image file format for saving plots. Accepts any file extension supported by `plt.savefig()`
    cache_dir : str, optional
        If given, local directory for caching BAR calculations. Results are cached by run, phase, and generation.
        If None, no caching is performed.
    min_num_work_values : int, optional
        Minimum number of forward and reverse work values for valid calculation
    """

    # load pandas dataframe from FAH
    work = pd.read_pickle(work_file_path)

    # convert columns to numeric
    for c in [
        "forward_work",
        "reverse_work",
        "forward_final_potential",
        "reverse_final_potential",
    ]:
        work[c] = pd.to_numeric(work[c])

    # load the json that contains the information as to what has been computed in each run
    if isinstance(details_file_path, str):
        details_file_path = [details_file_path]

    details = {}
    for path in details_file_path:
        with open(path, "r") as f:
            new = json.load(f)
            details = {**details, **new}

    # remove any unuseable values
    with pd.option_context("mode.use_inf_as_na", True):
        work = work.dropna()

    temperature = temperature_kelvin * unit.kelvin
    kT = kB * temperature

    projects = {"complex": complex_project, "solvent": solvent_project}

    def _produce_plot(name):
        if show_plots:
            plt.show()
        else:
            fname = os.extsep.join([name, plot_file_format])
            logging.info(f"Writing {fname}")
            plt.savefig(fname)

    def _bootstrap_BAR(run, phase, gen_id, n_bootstrap):
        f_works, r_works = _get_works(work, RUN, projects[phase], GEN=f"GEN{gen_id}")
        f_works = _strip_outliers(f_works)
        r_works = _strip_outliers(r_works)

        if len(f_works) < min_num_work_values:
            raise ValueError(
                f"less than {min_num_work_values} forward work values (got {len(f_works)})"
            )
        if len(r_works) < min_num_work_values:
            raise ValueError(
                f"less than {min_num_work_values} reverse work values (got {len(r_works)})"
            )

        fe, err = bootstrap_uncorrelated(BAR, n_iters=n_bootstrap)(
            f_works.values, r_works.values
        )

        return fe, err, f_works, r_works

    if cache_dir is not None:
        # cache results in local directory
        memory = joblib.Memory(backend="local", location=cache_dir, verbose=0)
        bootstrap_BAR = memory.cache(_bootstrap_BAR)
    else:
        bootstrap_BAR = _bootstrap_BAR

    def _max_gen(RUN):
        df = work[work["RUN"] == RUN]
        if df.empty:
            raise ValueError(f"No work values found for {RUN}")
        return df["GEN"].str[3:].astype(int).max()

    fig, axes = plt.subplots(ncols=2, nrows=1, figsize=(10, 5))

    def _process_run(RUN):
        def _process_phase(i, phase):
            all_forward = []
            all_reverse = []
            for gen_id in range(_max_gen(RUN)):
                fe, err, f_works, r_works = bootstrap_BAR(
                    RUN, phase, gen_id, n_bootstrap
                )
                d[f"{phase}_fe_GEN{gen_id}"] = fe
                d[f"{phase}_dfe_GEN{gen_id}"] = err
                all_forward.extend(f_works)
                all_reverse.extend(r_works)

            sns.kdeplot(all_forward, shade=True, color="cornflowerblue", ax=axes[i])
            sns.rugplot(
                all_forward,
                ax=axes[i],
                color="cornflowerblue",
                alpha=0.5,
                label=f"forward : N={len(f_works)}",
            )
            sns.rugplot(
                all_forward,
                ax=axes[i],
                color="darkblue",
                label=f"forward (gen0) : N={len(f_works)}",
            )
            sns.rugplot(
                [-x for x in all_reverse],
                ax=axes[i],
                color="mediumvioletred",
                label=f"reverse (gen0) : N={len(r_works)}",
            )
            sns.kdeplot(
                [-x for x in all_reverse], shade=True, color="hotpink", ax=axes[i]
            )
            sns.rugplot(
                [-x for x in all_reverse],
                ax=axes[i],
                color="hotpink",
                alpha=0.5,
                label=f"reverse : N={len(r_works)}",
            )
            axes[i].set_title(phase)

            # TODO add bootstrapping here
            d[f"{phase}_fes"] = BAR(np.asarray(all_forward), np.asarray(all_reverse))

        for i, phase in enumerate(projects.keys()):
            try:
                _process_phase(i, phase)
            except ValueError as e:
                logging.warning(f"Can't calculate {RUN} {phase}: {e}")
                continue

        fig.suptitle(
            f"{RUN}: {d['protein'].split('_')[0]} {d['start']}-{d['end']}", fontsize=16,
        )
        fig.subplots_adjust(top=0.9, wspace=0.15)
        axes[0].legend()
        axes[1].legend()
        _produce_plot(f"{RUN}")

    for d in tqdm(details.values()):
        RUN = d["directory"]
        try:
            _process_run(RUN)
        except ValueError as e:
            logging.warning(f"Can't calculate {RUN}: {e}")
            continue

    ligand_result = {0: 0.0}
    ligand_result_uncertainty = {0: 0.0}

    # TODO -- this assumes that everything is star-shaped, linked to ligand 0. If it's not, the values in ligand_result and ligand_result_uncertainty won't be correct.
    for d in details.values():
        if "complex_fes" in d and "solvent_fes" in d:
            DDG = ((d["complex_fes"][0] - d["solvent_fes"][0]) * kT).value_in_unit(
                unit.kilocalories_per_mole
            )
            dDDG = (
                (d["solvent_fes"][1] ** 2 + d["complex_fes"][1] ** 2) ** 0.5 * kT
            ).value_in_unit(unit.kilocalories_per_mole)
            ligand_result[d["end"]] = DDG
            ligand_result_uncertainty[d["end"]] = DDG

    _plot_relative_distribution(ligand_result.values())

    def _plot_relative_distribution(relative_fes, bins=100):
        """ Plots the distribution of relative free energies

        Parameters
        ----------
        relative_fes : list
            Relative free energies in kcal/mol
        bins : int, default=100
            Number of bins for histogramming


        """
        plt.hist(relative_fes, bins=bins)
        plt.xlabel("Relative free energy to ligand 0 / kcal/mol")
        _produce_plot("rel_fe_lig0_hist")

    ### this will be useful for looking at looking at shift in relative FEs over GENS

    for d in details.values():
        RUN = d["directory"]
        if "complex_fes" in d and "solvent_fes" in d:
            for i in range(_max_gen(RUN)):
                try:
                    DDG = (
                        (d[f"complex_fe_GEN{i}"] - d[f"solvent_fe_GEN{i}"]) * kT
                    ).value_in_unit(unit.kilocalories_per_mole)
                    dDDG = (
                        (
                            d[f"complex_dfe_GEN{i}"] ** 2
                            + d[f"solvent_dfe_GEN{i}"] ** 2
                        )
                        ** 0.5
                        * kT
                    ).value_in_unit(unit.kilocalories_per_mole)
                    plt.errorbar(i, DDG, yerr=dDDG)
                    plt.scatter(i, DDG)
                except:
                    continue
            _produce_plot(f"fe_delta_{RUN}")


if __name__ == "__main__":
    import fire

    fire.Fire(free_energies)
