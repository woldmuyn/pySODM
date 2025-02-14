import os
import gc
import sys
import emcee
import datetime
import json
import numpy as np
import matplotlib.pyplot as plt
from multiprocessing import get_context
from pySODM.optimization.visualization import traceplot, autocorrelation_plot

abs_dir = os.path.dirname(__file__)

def run_EnsembleSampler(pos, max_n, identifier, objective_function, objective_function_args=None, objective_function_kwargs=None,
                        moves=[(emcee.moves.DEMove(), 0.5),(emcee.moves.KDEMove(bw_method='scott'), 0.5)],
                        fig_path=None, samples_path=None, print_n=10, backend=None, processes=1, progress=True, settings_dict={}):
    """Wrapper function to setup an `emcee.EnsembleSampler` and handle all backend-related tasks.
    
    Parameters:
    -----------
        pos: np.ndarray
            Starting position of the Markov Chains. We recommend using `perturbate_theta()`.
        max_n: int
            Maximum number of iterations.
        identifier: str
            Identifier of the expirement.
        objective function: callable function
            Objective function. Recommended `log_posterior_probability`.
        objective_function_args: tuple
            Arguments of the objective function. If using `log_posterior_probability` as objective function, use default `None`.
        objective_function_kwargs: dict
            Keyworded arguments of the objective function. If using `log_posterior_probability` as objective function, use default `None`.
        samples_path: str
            Location where the `.hdf5` backend and settings `.json` should be saved.
        print_n: int
            Print autocorrelation and trace plots every `print_n` iterations.
        processes: int
            Number of cores to use.
        settings_dict: dict
            Dictionary containing calibration settings or other usefull settings for long-term storage. Saved in `.json` format. Appended to the samples dictionary generated by `emcee_sampler_to_dictionary()`. 
    
    Hyperparameters:
    ----------------
        moves: list
            Consult the [emcee documentation](https://emcee.readthedocs.io/en/stable/user/moves/).
        backend: emcee.backends.HDFBackend
            Backend of a previous sampling experiment. If a backend is provided, the sampler is restarted from the last iteration of the previous run. Consult the [emcee documentation](https://emcee.readthedocs.io/en/stable/user/backends/).
        progress: bool
            Enables the progress bar.

    Returns:
    --------

    sampler: emcee.EnsembleSampler
        Emcee sampler object ([see](https://emcee.readthedocs.io/en/stable/user/sampler/)). To extract a dictionary of samples + settings, use `emcee_sampler_to_dictionary`.

    """

    # Set default fig_path/samples_path as same directory as calibration script
    if not fig_path:
        fig_path = os.getcwd()
    else:
        fig_path = os.path.join(os.getcwd(), fig_path)
        # If it doesn't exist make it
        if not os.path.exists(fig_path):
            os.makedirs(fig_path)
    if not samples_path:
        samples_path = os.getcwd()
    else:
        samples_path = os.path.join(os.getcwd(), samples_path)
        # If it doesn't exist make it
        if not os.path.exists(samples_path):
            os.makedirs(samples_path)
    # Check if the fig_path/autocorrelation and fig_path/traceplots exist and if not make them
    for directory in [fig_path+"/autocorrelation/", fig_path+"/traceplots/"]:
        if not os.path.exists(directory):
            os.makedirs(directory)
    # Determine current date
    run_date = str(datetime.date.today())
    # By default, put the calibrated model parameters shapes in the settings dictionary so we can retrieve their sizes later
    settings_dict.update({'calibrated_parameters_shapes': objective_function.parameter_shapes})
    # Save setings dictionary to samples_path
    with open(samples_path+'/'+str(identifier)+'_SETTINGS_'+run_date+'.json', 'w') as file:
        json.dump(settings_dict, file)
    # Derive nwalkers, ndim from shape of pos
    nwalkers, ndim = pos.shape
    # By default: set up a fresh hdf5 backend in samples_path
    if not backend:
        filename = '/'+str(identifier)+'_BACKEND_'+run_date+'.hdf5'
        backend = emcee.backends.HDFBackend(samples_path+filename)
        backend.reset(nwalkers, ndim)
    # If user provides an existing backend: continue sampling 
    else:
        pos = backend.get_chain(discard=0, thin=1, flat=False)[-1, ...]
    # This will be useful to testing convergence
    old_tau = np.inf

    # Start calibration
    print(f'\nMarkov-Chain Monte-Carlo sampling')
    print(f'=================================\n')
    print(f'Using {processes} cores for {ndim} parameters, in {nwalkers} chains\n')
    sys.stdout.flush()

    with get_context("spawn").Pool(processes=processes) as pool:
        sampler = emcee.EnsembleSampler(nwalkers, ndim, objective_function, backend=backend, pool=pool,
                        args=objective_function_args, kwargs=objective_function_kwargs, moves=moves)
        for sample in sampler.sample(pos, iterations=max_n, progress=progress, store=True, tune=False):
            # Only check convergence every print_n steps
            if sampler.iteration % print_n:
                continue

            #############################
            # UPDATE DIAGNOSTIC FIGURES #
            #############################
            
            # Update autocorrelation plot
            ax, tau = autocorrelation_plot(sampler.get_chain(), labels=objective_function.expanded_labels,
                                            filename=fig_path+'/autocorrelation/'+identifier+'_AUTOCORR_'+run_date+'.pdf',
                                            plt_kwargs={'linewidth':2, 'color': 'red'})
            # Update traceplot
            traceplot(sampler.get_chain(),labels=objective_function.expanded_labels,
                        filename=fig_path+'/traceplots/'+identifier+'_TRACE_'+run_date+'.pdf',
                        plt_kwargs={'linewidth':2,'color': 'red','alpha': 0.15})
            # Garbage collection
            plt.close('all')
            gc.collect()

            #####################
            # CHECK CONVERGENCE #
            #####################

            # Hardcode threshold values defining convergence
            thres_multi = 50.0
            thres_frac = 0.03
            # Check if chain length > 50*max autocorrelation
            converged = np.all(np.max(tau) * thres_multi < sampler.iteration)
            # Check if average tau varied more than three percent
            converged &= np.all(np.abs(np.mean(old_tau) - np.mean(tau)) / np.mean(tau) < thres_frac)
            if converged:
                break
            old_tau = tau

    return sampler

def perturbate_theta(theta, pert, multiplier=2, bounds=None, verbose=None):
    """ A function to perturbate a PSO estimate and construct a matrix with initial positions for the MCMC chains

    Parameters
    ----------

    theta : list (of floats) or np.array
        Result of PSO calibration, results must correspond to the order of the parameter names list (pars)

    pert : list (of floats)
        Relative perturbation factors (plus-minus) on PSO estimate

    multiplier : int
        Multiplier determining the total number of markov chains that will be run by emcee.
        Typically, total nr. chains = multiplier * nr. parameters
        Default (minimum): 2 (one chain will result in an error in emcee)
        
    bounds : array of tuples of floats
        Ordered boundaries for the parameter values, e.g. ((0.1, 1.0), (1.0, 10.0)) if there are two parameters.
        Note: bounds must not be zero, because the perturbation is based on a percentage of the value,
        and any percentage of zero returns zero, causing an error regarding linear dependence of walkers
        
    verbose : boolean
        Print user feedback to stdout

    Returns
    -------
    ndim : int
        Number of parameters

    nwalkers : int
        Number of chains

    pos : np.array
        Initial positions for markov chains. Dimensions: [ndim, nwalkers]
    """

    # Validation
    if len(theta) != len(pert):
        raise Exception('The parameter value array "theta" must have the same length as the perturbation value array "pert".')
    if bounds and (len(bounds) != len(theta)):
        raise Exception('If bounds is not None, it must contain a tuple for every parameter in theta')
    # Convert theta to np.array
    theta = np.array(theta)
    # Define clipping values: perturbed value must not fall outside this range
    if bounds:
        lower_bounds = [bounds[i][0]/(1-pert[i]) for i in range(len(bounds))]
        upper_bounds = [bounds[i][1]/(1+pert[i]) for i in range(len(bounds))]
    
    ndim = len(theta)
    nwalkers = ndim*multiplier
    cond_number=np.inf
    retry_counter=0
    while cond_number == np.inf:
        if bounds and (retry_counter==0):
            theta = np.clip(theta, lower_bounds, upper_bounds)
        pos = theta + theta*pert*np.random.uniform(low=-1,high=1,size=(nwalkers,ndim))
        cond_number = np.linalg.cond(pos)
        if ((cond_number == np.inf) and verbose and (retry_counter<20)):
            print("Condition number too high, recalculating perturbations. Perhaps one or more of the bounds is zero?")
            sys.stdout.flush()
            retry_counter += 1
        elif retry_counter >= 20:
            raise Exception("Attempted 20 times to perturb parameter values but the condition number remains too large.")
    if verbose:
        print('Total number of markov chains: ' + str(nwalkers)+'\n')
        sys.stdout.flush()
    return ndim, nwalkers, pos

def emcee_sampler_to_dictionary(samples_path, identifier, discard=0, thin=1, run_date=str(datetime.date.today())):
    """
    A function to discard and thin the samples available in the sampler object and subsequently convert them to a dictionary of format: {parameter_name: [sample_0, ..., sample_n]}.
    Append a dictionary of settings (f.i. starting estimate of MCMC sampler, start- and enddate of calibration).
    """

    ###############################
    ## Load sampler and settings ##
    ###############################

    import json

    # Load settings
    with open(os.path.join(os.getcwd(), samples_path+str(identifier)+'_SETTINGS_'+run_date+'.json'), 'r') as f:
        settings = json.load(f)

    # Load sampler 
    filename =  samples_path+'/'+str(identifier)+'_BACKEND_'+run_date+'.hdf5'
    sampler = emcee.backends.HDFBackend(os.path.join(os.getcwd(), filename))

    ####################
    # Discard and thin #
    ####################

    try:
        autocorr = sampler.get_autocorr_time()
        thin = max(1, round(0.5 * np.max(autocorr)))
        print(f'Convergence: the chain is longer than 50 times the intergrated autocorrelation time.\nPreparing to save samples with thinning value {thin}.')
        sys.stdout.flush()
    except:
        thin=1
        print('Warning: The chain is shorter than 50 times the integrated autocorrelation time.\nUse this estimate with caution and run a longer chain! Setting thinning to 1.\n')
        sys.stdout.flush()

    #####################################
    # Construct a dictionary of samples #
    #####################################

    flat_samples = sampler.get_chain(discard=discard,thin=thin,flat=True)
    samples_dict = {}
    count=0
    for name,value in settings['calibrated_parameters_shapes'].items():
        if value != [1]:
            vals=[]
            for j in range(np.prod(value)):
                vals.append(list(flat_samples[:, count+j]))
            count += np.prod(value)
            samples_dict[name] = vals
        else:
            samples_dict[name] = list(flat_samples[:, count])
            count += 1

    # Remove calibrated parameters from the settings
    del settings['calibrated_parameters_shapes']
    # Append settings to samples dictionary
    samples_dict.update(settings)
    # Remove settings .json
    os.remove(os.path.join(os.getcwd(), samples_path+str(identifier)+'_SETTINGS_'+run_date+'.json'))

    ###############
    # Save result #
    ###############

    # Save setings dictionary to samples_path
    with open(os.path.join(os.getcwd(),samples_path+str(identifier)+'_SAMPLES_'+run_date+'.json'), 'w') as file:
        json.dump(samples_dict, file)

    return samples_dict