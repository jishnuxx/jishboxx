#
# fitting.py
#
# H. Sandmeyer, D. Clarke
#
# Fitter class, along with functions for fitting data. The goal is to be able to carry out simple fits, trying many
# fitting algorithms. Bayesian priors are supported. You can judge the quality of your fit by quantities like
# chi^2/d.o.f. and logGBF. Errors in the fit parameters can be calculated via error propgation, or through the Hessian
# of the chi^2. It also has a variety of "saved" attributes which can be used for repeated, automated curve fitting.
#

import numpy as np
from scipy.optimize import curve_fit
from scipy.linalg import inv
import latqcdtools.base.logger as logger
from latqcdtools.base.speedify import DEFAULTTHREADS, parallel_function_eval
from latqcdtools.base.plotting import plot_dots, fill_param_dict, plot_bar, plt
from latqcdtools.base.readWrite import writeTable
from latqcdtools.base.utilities import envector, unvector, isHigherDimensional, printDict
from latqcdtools.math.optimize import minimize
from latqcdtools.math.num_deriv import diff_jac, diff_fit_hess, diff_fit_grad
from latqcdtools.statistics.statistics import plot_func, error_prop_func, norm_cov, \
    cut_eig, chisquare, logGBF, funcExpand
from inspect import signature
import matplotlib as mpl


class Fitter:
    """ The :class:`Fitter`, contains all information necessary for fitting: The data, the function to be fitted, and
    optional the data for the errors. There are different minimization algorithms available. Many of them need the
    gradient or hessian of the chisquare. One way is to set the derivatives of the fitting function from outside.
    The derivatives of the actual chisquare are then computed via error propagation. Another way is to use numerical
    derivatives.

    There are two ways to compute the derivatives of the chisqare numerically. Either compute the
    numerical derivative of the whole chisquare (error_strat='hessian') or compute the derivatives of the fitting
    function and use error propagation (error_strat='propagation'). The latter is expected to be more stable and is the
    default case.

    Parameters
    ----------
    func : callable
        Function to be fitted. Depending on parameter expand the format has to be
            func(x, a, b, *args)
        or
            func(x, params, *args)
    xdata : array_like
        xdata used for fit. These data may be higher dimensional. This may be the case when our fit functions needs
        more than one parameter. However, the number of elements in the first axis has to be equal to the number of
        elements in ydata.
    ydata : array_like
        ydata used for fit.
    edata : array_like, optional, default: None
        Data for the error. Either pass an 1D array of errors of a full covariance matrix. In case of errors, the
        errors are interpreted as edata = sqrt(variance). For the case of the covariance matrix no root has to be
        taken: variance = diag(edata).
    grad : callable, optional, default: None
        gradient of the fit function.
    hess : callable, optional, default: None
        hessian of the fit function.
    args : array_like, optional, default: ()
        Optional arguments that shall be passed to func and that should not be fitted.
    grad_args : array_like, optional, default: None
        Optional parameter for the gradient. If set to None the arguments for the function are used (args).
    hess_args : array_like, optional, default: None
        Optional parameter for the hessian. If set to None the arguments for the function are used (args).
    expand : bool, optional, default: True
        Expand the parameter for the fitting function. If true, function has to look like
            func(x, a, b, *args)
        otherwise it has to look like
            func(x, params, *args).
    tol : float, optional, default: 1e-12
        Tolerance for the minimization.
    max_fev: int, optional, default: 10000
        Maximum number of iterations / function evaluations.
    use_diff : bool, optional, default: True
        In case of numerical derivative use the difference quotient for approximation.
    norm_err_chi2 : bool, optional, default: True
        Multiply errors with chi**2/d.o.f. This is the usual case for fitting algorithms.
    derive_chisq : bool, optional, default: False
        In case of numerical derivative, apply the derivative to the whole chisquare instead of the function.
    eig_threshold : bool, optional, default: 1e-18
        If we encounter an eigenvalue of the correlation matrix smaller than threshold, replace it with threshold.

    Returns
    -------
    :class:`Fitter` object

    Examples
    --------
    For usage, create an instance of class fitter an then call do_fit or try_fit.
    >>> func = lambda x,a:a*x**2
    >>> fitter = Fitter(func, [1,2,3,4], [1,3,2,1], [0.4, 0.5, 0.3, 0.2])
    >>> fitter.do_fit(start_params = [1])
    (array([0.08876904]), array([0.04924371]), 17.872433846281407)
    """

    # Allowed keys for the constructor
    _allowed_keys = ['grad', 'hess', 'args', 'expand', 'grad_args', 'hess_args', 'tol', 'use_diff', 'error_strat',
                     'norm_err_chi2', 'derive_chisq', 'eig_threshold', 'test_tol', 'max_fev', 'nproc']

    # All possible algorithms.
    _all_algs = ["curve_fit", "L-BFGS-B", "TNC", "Powell" ,"Nelder-Mead", "COBYLA", "SLSQP", "CG","dogleg", "trust-ncg"]

    # Standard algorithms for the minimization. All but COBYLA are rather fast.
    _std_algs = ["curve_fit", "TNC", "Powell" ,"Nelder-Mead", "COBYLA"]


    def __init__(self, func, xdata, ydata, edata = None, **kwargs):

        diff = set(set(kwargs.keys()) - set(self._allowed_keys))
        if len(diff) != 0:
            logger.TBError("Illegal argument(s) to fitter", *diff)

        # Some attributes that are set in functions other than __init__.
        self._numb_params = 0
        self._grad        = None
        self._hess        = None
        self.hess         = None
        self.grad         = None
        self._pcov        = None

        # Store data
        self._xdata     = np.array(xdata, dtype = float)
        self._ydata     = np.array(ydata, dtype = float)
        self._numb_data = len(self._ydata)

        # These attributes are described in the above doccumentation. If they aren't specified in the keyword
        # arguments when the Fitter is initialized, they take the default value shown here. 
        self._use_diff = kwargs.get('use_diff', True)
        self._derive_chisq = kwargs.get('derive_chisq', False)
        self._expand = kwargs.get('expand', True)
        self._tol = kwargs.get('tol', 1e-10)
        self._test_tol = kwargs.get('test_tol', 1e-10)
        self._max_fev = kwargs.get('max_fev', None)
        self._norm_err_chi2 = kwargs.get('norm_err_chi2', False)
        self._args = kwargs.get('args', ())
        self._grad_args = kwargs.get('grad_args', None)
        self._errorAlg = kwargs.get('error_strat', 'propagation')
        self._eig_threshold = kwargs.get('eig_threshold', 1e-18)
        self._nproc = kwargs.get('nproc', DEFAULTTHREADS)

        if self._grad_args is None:
            self._grad_args = self._args
        self._hess_args = kwargs.get('hess_args', None)
        if self._hess_args is None:
            self._hess_args = self._args

        if type(self._max_fev) is int:
            tmp_fev = self._max_fev
            self._max_fev = dict()
            for alg in self._all_algs:
                self._max_fev[alg] = tmp_fev

        if self._max_fev is None:
            self._max_fev = {"curve_fit" : 50000,
                    "L-BFGS-B": 15000,
                    "TNC": 15000,
                    "Powell" : 30000,
                    "Nelder-Mead": 15000,
                    "COBYLA": 15000,
                    "SLSQP": 15000,
                    "CG": 15000,
                    "dogleg": 15000,
                    "trust-ncg": 15000
                    }

        # Initialize func. This is also done in set_func, but we need it before that
        self._func = func

        # Get number of parameters
        self._get_numb_params()

        # This variable stores the result from the last fit. This is used as start parameters for the next fit, if no
        # new start parameters are provided
        self._saved_params = np.ones(self._numb_params)

        # Current status of the fit errors. Initialize with inf
        self._saved_pcov = np.full((self._numb_params, self._numb_params), np.inf)

        self.set_func(func, kwargs.get('grad', None), kwargs.get('hess', None))

        # For Bayesian fits. If these are used, they are set in general_fit, and then utilized elsewhere.
        self._priorval   = np.ones(self._numb_params)
        self._priorsigma = np.ones(self._numb_params)
        self._checkprior = False

        # Check if we have the covariance matrix available and compute weights etc.
        if edata is not None:
            edata = np.asarray(edata, dtype = float)
            if isHigherDimensional(edata): 
                self._cov_avail = True
                self._cov       = edata
            else: 
                self._cov_avail = False
                self._cov       = np.diag(np.array(edata)**2)
        else:
            # Initialize everything to one if we don't get error information. 
            self._cov_avail = False
            self._cov       = np.diag(np.ones(len(self._ydata)))

        # Correlation matrix
        self._cor = norm_cov(self._cov)
        self._fit_cor = cut_eig(self._cor, self._eig_threshold)
        self._fit_inv_cor = inv(self._fit_cor)

        logger.details('Fitter initialized.')
        logger.details('  use_diff:',self._use_diff) 
        logger.details('  derive_chisq:',self._derive_chisq)
        logger.details('  expand:',self._expand)
        logger.details('  tol:',self._tol)
        logger.details('  test_tol:',self._test_tol)
        logger.details('  max_fev:',self._max_fev)
        logger.details('  norm_err_chi2:',self._norm_err_chi2)
        logger.details('  args:',self._args)
        logger.details('  grad_args:',self._grad_args)
        logger.details('  errorAlg:',self._errorAlg)
        logger.details('  eig_threshold:',self._eig_threshold)
        logger.details('  nproc:',self._nproc) 


    def _get_numb_params(self):
        """ Find out the number of parameters that the fit function takes. In case of non expanded parameters, we simply
        try how large the parameter array has to be without generating an exception. Result is stored in
        self._numb_params. """
        ntries = 1000
        if self._expand:
            # signature().parameters gives the total number of parameters passed to a function. We subtract 1 (self)
            # along with the number of arguments
            self._numb_params = len(signature(self._func).parameters) - 1 - len(self._args)
            return
        else:
            params = []
            for i in range(ntries):
                params.append(1)
                try:
                    i += 1
                    self._func(self._xdata, params, *self._args)
                    self._numb_params = i
                    logger.details("number params = ", self._numb_params)
                    return
                except Exception as e:
                    if i == ntries:
                        logger.debug("Last error was", e)
            logger.TBError("Fit function does not work with up to", ntries,"parameters. Enable DEBUG level for more details.")


    def set_func(self, func, grad = None, hess = None, args = None, grad_args = None, hess_args = None):
        """ Set fitting function, gradient, hessian and their arguments. Also initialize self.func,
        self.hess and self.grad. These point to the actual wrappers which are used in the fit. In
        case of provided gradient or Hessian, this will be wrap_grad or wrap_hess, respectively. In
        case of numerical derivatives, this will be num_grad and num_hess.

        Parameters
        ----------
        func : callable
            Function to be fitted.
        grad : callable, optional, default: None
            gradient of the fit function.
        hess : callable, optional, default: None
            hessian of the fit function.
        args : array_like, optional, default: ()

        grad_args : array_like, optional, default: None
            Optional parameter for the gradient. If set to None the arguments for the function
            are used (args).
        hess_args : array_like, optional, default: None
            Optional parameter for the hessian. If set to None the arguments for the function
            are used (args).
        """

        # Direct storage of the user functions.
        self._func = func
        self._grad = grad
        self._hess = hess

        # Later we only access self.func, self.grad, and self.hess. These are wrappers around the user function or the
        # numerical derivatives. The code below chooses the right wrappers. When they are set to None, None will
        # ultimately be passed to scipy.optimize.minimize, which indicates that minimize should estimate these
        # quantities with numerical derivatives.
        if self._hess is None:
            if self._use_diff and not self._derive_chisq:
                self.hess = self.num_hess
            else:
                self.hess = None
                self._derive_chisq = True
        else:
            self.hess = self.wrap_hess

        if self._grad is None:
            if self._use_diff and not self._derive_chisq:
                self.grad = self.num_grad
            else:
                self.grad = None
                self._derive_chisq = True
        else:
            self.grad = self.wrap_grad

        if args is not None:
            self._args = args

        if grad_args is not None:
            self._grad_args = grad_args
        elif self._args is not None:
            self._grad_args = self._args

        if hess_args is not None:
            self._hess_args = hess_args
        elif self._args is not None:
            self._hess_args = self._args

        self.check_start_params()


    def check_start_params(self):
        """ Check if the start parameters work with the fitting function. If not: Generate new default start_parameters.
        These are stored in self._saved_params. """
        try:
            funcExpand(self._func,self._xdata,self._saved_params,self._args,self._expand)
        except Exception as e:
            logger.warn("Function cannot handle start_parameters",self._saved_params)
            self._get_numb_params()
            self._saved_params = np.ones(self._numb_params)
            raise e

        if any(np.isnan(self._saved_params) | np.isinf(self._saved_params)):
            logger.info("Nan or inf in start parameters. Generate new defaults")
            self._get_numb_params()
            self._saved_params = np.ones(self._numb_params)


    def num_grad(self, x, params):
        return diff_fit_grad(x, params, self._func, self._args, expand = self._expand)
    def num_hess(self, x, params):
        return diff_fit_hess(x, params, self._func, self._args, expand = self._expand)


    def wrap_grad(self, x, params):
        return funcExpand(self._grad,x,params,self._grad_args,expand=self._expand)
    def wrap_hess(self, x, params):
        return funcExpand(self._hess,x,params,self._hess_args,expand=self._expand)
    def wrap_func(self, x, params):
        return funcExpand(self._func,x,params,self._args,expand=self._expand)


    def fit_ansatz_array(self, params):
        """ Return the array of the fit ansatz values at each position in self._xdata. """
        params = np.asarray(params)

        # If the function supports numpy objects as input, we can call it directly. Otherwise we have to loop over
        # all values in self._xdata
        ret = self.wrap_func(self._xdata, params)
        self._cache_array = np.copy(ret)
        self._cache_p_array = params
        return ret


    def jacobian_fit_ansatz_array(self, params):
        """ If f is the fit function, return the array df / dp_i evaluated at each _fit_xdata. """
        params = np.asarray(params)
        # Notes that the function is passed in secret here.
        ret = self.grad(self._xdata, params).transpose()
        self._cache_jac = np.copy(ret)
        self._cache_p_jac = params
        return ret


    def hess_fit_ansatz_array(self, params):
        """ If f is the fit function, return the array d^2f / dp_i dp_j evaluated at each _fit_xdata. """
        params = np.asarray(params)
        # hess is just a wrapper. hess_args are considered in this wrapper
        ret = self.hess(self._xdata, params).transpose()
        self._cache_hess = np.copy(ret)
        self._cache_p_hess = params
        return ret


    def calc_chisquare(self, params):
        """ Compute the chisquare, i.e. the chi^2. This is the function that will be minimized. """
        if self._checkprior:
            prior, prior_err = self._priorval, self._priorsigma
        else:
            prior, prior_err = None, None
        return chisquare(self._xdata, self._ydata, self._cov, self._func, self._args, params, prior=prior,
                         prior_err=prior_err, expand=self._expand)


    def grad_chisquare(self, params):
        """ Compute the gradient of the chisquare. Used by some solvers in the minimization routine. """
        jac  = self.jacobian_fit_ansatz_array(params).transpose()    # df/dp
        diff = self.fit_ansatz_array(params) - self._ydata       # Dy

        sigma = np.sqrt(np.diag(self._cov))
        jac  /= sigma
        diff /= sigma

        res = 2*jac.dot(self._fit_inv_cor.dot(diff))

        if self._checkprior:
            res += np.sum( 2*( np.array(params) - self._priorval ) / self._priorsigma**2 )
        return res


    def hess_chisquare(self, params):
        """ Compute the hessian of the chisquare. Used by some solvers in the minimization routine. """
        hess = self.hess_fit_ansatz_array(params).transpose()
        jac  = self.jacobian_fit_ansatz_array(params).transpose()
        diff = self.fit_ansatz_array(params) - self._ydata

        sigma = np.sqrt(np.diag(self._cov))
        hess /= sigma
        jac  /= sigma
        diff /= sigma

        res = 2*( hess.dot( self._fit_inv_cor.dot( diff.transpose() ) ) + jac.dot( self._fit_inv_cor.dot( jac.transpose() ) ) )
        if self._checkprior:
            res += np.sum( 2/self._priorsigma**2 )

        return res


    def _num_func_jacobian(self, params):
        """ For the error computation we need the Jacobian of the array of function values. If self._derive_chisq is True,
        we cannot use self.grad_fit_ansatz_array. In that case, the Jacobian is calculated using this function. """
        return diff_jac(params, self.fit_ansatz_array)


    def minimize_chi2(self, start_params, algorithm):
        """ Minimize the chi^2 using the scipy minimize routine is used.

        Parameters
        ----------
        start_params: array_like
            The start parameters that are used for the minimization.
        algorithm: string
            The algorithm that will be used.

        Returns
        -------
        params:
            Array of the parameters that minimize the chisquare.
        chi2:
            The minimum value of the chisquare.
        """

        if self.grad is not None:
            jac_func=self.grad_chisquare
        else:
            jac_func=None

        if self.hess is not None:
            hess_func=self.hess_chisquare
        else:
            hess_func=None

        if algorithm == "curve_fit":

            if self._checkprior:
                logger.TBError('The curve_fit algorithm is not yet able to handle priors.')
            func = lambda x, *p: self.wrap_func(x, p)

            cov = self._cov
            # If no gradient has been provided by the user, it is probably better to use the numerical derivative from
            # curve_fit instead of our own.
            if self._grad is not None:
                grad = lambda x, *p: np.array(self.grad(x, p)).transpose()
            else:
                grad = None

            params, _ = curve_fit(func, self._xdata, self._ydata, sigma = cov, p0 = start_params, jac = grad,
                                  ftol = self._tol, maxfev = self._max_fev["curve_fit"])

        else:
            params = minimize(self.calc_chisquare, jac_func, hess_func, start_params, self._tol,
                              self._max_fev[algorithm], algorithm = algorithm)

        if any(np.isnan(params)) or any(np.isinf(params)):
            raise ValueError(algorithm + ": Fit result is inf or nan!")

        chi2 = self.calc_chisquare(params)

        return params, chi2


    def getDOF(self,params):
        """ Compute the number of degrees of freedom. Depends on whether you use priors. """
        # You can think of priors as extra data points. Hence if you use a prior for every fit parameter, it follows
        # that the number of degrees of freedom always equals the number of data.
        if self._checkprior:
            dof = len(self._ydata)
        else:
            dof = len(self._ydata) - len(params)
        if dof < 0:
            logger.TBError('Fewer data than fit parameters!')
        logger.debug('Computed d.o.f. =',dof)
        return dof


    def compute_err(self, params, chi2, algorithm):
        """ Compute the covariance matrix of the fit parameters. If no errors have been provided, they are assumed to
        be one. We get the fit variances from the diagonal elements of the covariance matrix.

        Parameters
        ----------
        params: array_like
            Parameters for which the errors should be computed.
        chi2: float
            The chisquare for those parameters.
        algorithm: string
            The algorithm used to compute these errors (Necessary for strings in exceptions).

        Returns
        ------
        pcov:
            Correlation matrix of the fit parameters.
        fit_errors:
            Diagonal of pcov.
        """

        dof = self.getDOF(params)

        logger.debug('using error alg',self._errorAlg)
        if dof <= 0:
            pcov = np.full((len(params),len(params)), np.nan)
        else:
            if self._errorAlg=='propagation':
                pcov = self.pcov_error_prop(params,algorithm)
            elif self._errorAlg=='hessian':
                pcov = self.pcov_hessian(params,algorithm)
            else:
                logger.TBError('Unknown fitting algorithm', self._errorAlg)

        # Sometimes people like to rescale the parameter covariance matrix by the chi^2/dof. This tries to
        # take the fit quality in account into the error directly, and to my understanding it is what
        # gnuplot does. In a physics context, this procedure seems to be unjustified, and it
        # doesn't come naturally out of the mathematics, so this is not done by default.
        if self._norm_err_chi2:
            pcov *= chi2/dof
        fit_errors = np.sqrt(np.diag(pcov))

        self._pcov = pcov

        return pcov, fit_errors


    def pcov_error_prop(self, params, algorithm):
        """ Compute the parameter's covariance matrix through error propagation, i.e. pcov = (J^t * C^-1 * J)^-1, where
        J is the Jacobian of the fit function and C is the covariance matrix of the data points. """
        if self.grad is None:
            jac = self._num_func_jacobian(params)
        else:
            jac = self.jacobian_fit_ansatz_array(params)

        inv_cov_mat = self._fit_inv_cor
        jac = jac.transpose() / np.sqrt(np.diag(self._cov))
        jac = jac.transpose()

        jej = jac.transpose().dot(inv_cov_mat.dot(jac))

        try:
            inv_jej = np.linalg.inv(np.matrix(jej))
            test    = np.array((inv_jej*np.matrix(jej)).tolist(), dtype = float)
            inv_jej = np.array(inv_jej.tolist(), dtype = float)
            pcov    = inv_jej

            if abs(np.sum(test) - np.sum(np.diag(test))) > self._test_tol:
                logger.warn("Off diagonals in test matrix are larger than",self._test_tol,"Test - matrix:")
                logger.warn('\n',test)
                raise ValueError(algorithm + ": Precision lost when computing errors!")

            if np.min(np.diag(pcov)) < 0:
                raise ValueError(algorithm + ": Negative entries for the variance!")

        except Exception as e:
            raise ValueError(algorithm + "hit exception " + e ) 

        return pcov


    def pcov_hessian(self, params, algorithm):
        """ Obtain the parameter's covariance matrix by inverting the hessian of the chi^2. """
        pcov = inv(self.hess_chisquare(params))
        if np.min(np.diag(pcov)) < 0:
            raise ValueError(algorithm + ": Negative entries for the variance!")
        return pcov


    def _general_fit(self, start_params=None, algorithm="curve_fit", priorval=None, priorsigma=None):
        """ Perform fit. No new fit data are generated. """
        params, chi2 = self.minimize_chi2(self._saved_params, algorithm)
        pcov, fit_errors = self.compute_err(params, chi2, algorithm)
        return params, fit_errors, chi2, pcov


    def _tryAlgorithm(self,algorithm,start_params,priorval,priorsigma):
        """ Wrapper that collects general fit results. Allows for parallelization. """
        logger.details("Trying", algorithm, "...")
        try:
            params, fit_errors, chi2, pcov = self._general_fit(start_params, algorithm, priorval, priorsigma)
            logger.details(algorithm, "successful. Chi^2 = ", chi2)
            return params, fit_errors, chi2, pcov, None
        except Exception as e:
            logger.details(algorithm, "failed with exception", e)
            return None, None, np.inf, None, e


    def try_fit(self, algorithms = None, start_params = None, priorval = None, priorsigma = None, detailedInfo = False):
        """ Perform the fit. This is what you should usually call. Try different algorithms and choose the one with the
        smallest chi^2. By default this method does a standard statistical fit. One can also include priors to obtain
        posteriors using Bayesian statistics. A well known summary of the latter strategy in the context of lattice QCD
        is given https://arxiv.org/abs/hep-lat/0110175. If there are priors, calculate logGBF and return that too.

        Parameters
        ----------
        algorithms: string or None, optional, default = None
            List of strings with the algorithms that can be used. Possible values are:
                "L-BFGS-B", "TNC", "Powell" ,"Nelder-Mead", "COBYLA", "SLSQP", "CG", "BFGS", "dogleg", "trust-ncg".
            The latter 4 usually don't work. When provided None, the first 7 algorithms are used.
        start_params: array_like, optional, default = None
            The start parameters for the fit.
        priorval: array_like, optional, default = None
            For constrained fits. Prior mean values for the fit.
        priorsigma: array_like, optional, default = None
            For constrained fits. Prior error bars for the fit.
        detailedInfo : bool, optional. default = False
            If True, return also the covariance matrix of the fit parameters and logGBF

        Returns
        -------
        params:
            The final fit parameters.
        params_err:
            The error of this fit parameters.
        chidof:
            chi^2/dof.
        pcov:
            The covariance matrix of the parameters.
        logGBF:
            log of Gaussian Bayes factor.
        """

        if algorithms is None:
            algorithms = self._std_algs
        elif algorithms == 'all':
            algorithms = self._all_algs

        logger.debug('Using algorithms',algorithms)
        logger.debug('priorval =',priorval)
        logger.debug('priorsigma =',priorsigma)

        # Prior values are a good point for starting the fit, if you don't have another guess.
        if priorval is not None and start_params is None:
            start_params = priorval

        # For a fit with only one parameter, we also accept a scalar. Check if this is the case.
        if start_params is not None:
            self._saved_params = envector(start_params)

        # Check for consistency.
        self.check_start_params()

        # If the fit function has parameters that have default values that should also be fitted, the automatically
        # computed numb_params is wrong. Therefore we make sure that self._numb_params corresponds to self._saved_params
        # at this point.
        self._numb_params = len(self._saved_params)

        # Initialize prior values.
        if priorsigma is not None:
            if priorval is None:
                logger.TBError("priorsigma passed but priorval is None")
            self._priorsigma = np.array(priorsigma)
            self._priorval = np.array(priorval)
            self._checkprior = True
        else:
            if priorval is not None:
                logger.TBError("priorval passed but priorsigma is None")
            self._priorval   = np.zeros(self._numb_params)
            self._priorsigma = np.ones(self._numb_params)
            self._checkprior = False

        # Check for consistency.
        if self._checkprior:
            if len(self._priorsigma) != self._numb_params:
                logger.TBError("Number priorsigma != number of fit parameters")

            if len(self._priorval) != self._numb_params:
                logger.TBError("Number priorval != number of fit parameters")

        resultSummary  = parallel_function_eval( self._tryAlgorithm, algorithms, args=(start_params, priorval, priorsigma), nproc=self._nproc )
        all_params     = [row[0] for row in resultSummary]
        all_fit_errors = [row[1] for row in resultSummary]
        all_chi2       = [row[2] for row in resultSummary]
        all_pcov       = [row[3] for row in resultSummary]
        all_except     = [row[4] for row in resultSummary]

        # Check to see if all the fits failed
        if np.all(np.array(all_chi2) == np.inf): 
            for i, algorithm in enumerate(algorithms):
                logger.TBFail(algorithm+"--",all_except[i])
            logger.TBError("No algorithm converged.See above list of exceptions.")

        # Find the smallest chi^2
        min_ind = np.argmin(all_chi2)
        if len(algorithms) > 1:
            logger.details("Choosing", algorithms[min_ind], "with chi^2 =", all_chi2[min_ind])

        # Store results internally
        self._saved_params = all_params[min_ind]
        self._saved_pcov = all_pcov[min_ind]

        for i in range(len(algorithms)):
            logger.debug(algorithms[i]+':',all_params[i],all_fit_errors[i],all_chi2[i],all_pcov[i],all_except[i])
            
        dof = self.getDOF(self._saved_params)

        if dof <= 0:
            chidof = np.inf
        else:
            chidof = all_chi2[min_ind]/dof

        if detailedInfo:
            return ( np.copy(self._saved_params),
                     all_fit_errors[min_ind],
                     chidof,
                     self.logGBF(self._saved_params),
                     np.copy(self._saved_pcov)
                   )
        else:
            return ( np.copy(self._saved_params),
                     all_fit_errors[min_ind],
                     chidof
                   )


    def do_fit(self, algorithm="curve_fit", **kwargs):
        """ Same as try_fit but with only one algorithm. """
        return self.try_fit([algorithm], **kwargs)


    def logGBF(self,params):
        return logGBF(self._xdata, self._ydata, self._cov, self._func, self._args, params,
                      prior=self._priorval,prior_err=self._priorsigma, expand=self._expand)


    def plot_func(self, params = None, params_err = None, xmin = None, xmax = None, color = None, alpha = 0.1,
                  no_error = False, **kwargs):
        """ Plot the fit function. """
        if params_err is None and params is None:
            params_err = self._saved_pcov
        if params is None:
            params = self._saved_params
        if params_err is None:
            params_err = []
        if xmin is None:
            xmin = np.min(self._xdata)
        if xmax is None:
            xmax = np.max(self._xdata)

        # Error propagation is not implemented for non-expanded parameters. Therefore, we use expanded parameters here.
        if self._expand:
            func = lambda x, *params: self._func(x, *(tuple(params) + tuple(self._args)))
        else:
            func = lambda x, *params: self._func(x, params, *self._args)
        if not no_error:
            # Call the grad wrapper instead of directly self._grad
            grad = lambda x, *params: np.asarray(self.grad(x, params))

            plot_func(func, xmin = xmin, xmax = xmax, args = params, args_err = params_err, grad = grad, color = color,
                      alpha = alpha, expand = True, **kwargs)
        else:
            plot_func(func, xmin = xmin, xmax = xmax, args = params, color = color, expand = True, **kwargs)


    def save_func(self, filename, params = None, params_err = None, xmin = None, xmax = None, color = None, alpha = 0.1,
                  no_error = False, header=None, **kwargs):
        """ Save fit data to table. """

        if params_err is None and params is None:
            params_err = self._saved_pcov

        if params is None:
            params = self._saved_params

        if params_err is None:
            params_err = []

        if xmin is None:
            xmin = np.min(self._xdata)
        if xmax is None:
            xmax = np.max(self._xdata)

        # Error propagation is not implemented for non-expanded parameters. Therefore, we use expanded parameters.
        if self._expand:
            func = lambda x, *params: self._func(x, *(tuple(params) + tuple(self._args)))
        else:
            func = lambda x, *params: self._func(x, params, *self._args)

        if not no_error:
            # Call the grad wrapper instead of directly self._grad
            grad = lambda x, *params: np.asarray(self.grad(x, params))

            save_func(func, filename, xmin = xmin, xmax = xmax, args = params, args_err = params_err, grad = grad,
                      color = color, alpha = alpha, expand = True, header = header, **kwargs)
        else:
            save_func(func, filename, xmin = xmin, xmax = xmax, args = params, color = color, header = header,
                      expand = True, **kwargs)

        
    def plot_data(self, ylog = False, **kwargs):
        """ Plot the fit data. """

        if ylog:
            plt.yscale('log')

        if self._cov is not None:
            sigma = np.sqrt( np.diag(self._cov) )
            plot_dots(self._xdata, self._ydata, sigma, **kwargs)
        else:
            plot_dots(self._xdata, self._ydata, **kwargs)


    def plot_fit(self, params = None, params_err = None, ranges = None, ylog = False, 
                 no_error = False, args_data = None, args_func = None, xmin = None, xmax = None, **kwargs):
        """ Plot the fit and the fit data.

        Parameters
        ----------
        params : array_like, optional
            Parameters for the fit function.
        params_err : array_like, optional
            Errors of the above parameters.
        notex : bool, optional
            Do not use tex for plotting.
        xmin : float, optional
            xmin for the data plot.
        xmax : float, optional
            xmax for the data plot.
        ranges : array_like, optional
            2D array of ints. If you want to plot multiple fit results, this array should contain the boundaries for
            each fit. It should look like
                [[xmin_1, xmax_1], [xmin_2, xmax_2]...]
            In that case params and params_err also need to be arrays of parameters.
        ylog : bool, optional
            Use an logarithmic y-scale.
        no_error : bool, optional
            Don't plot error bars.
        **kwargs
            Additional arguments that can be passed to the plotting functions. See plotting.py.
        """

        if args_func is None:
            args_func = {}
        if args_data is None:
            args_data = {}

        args_data.update(kwargs)

        if xmin is not None:
            args_data['xmin'] = xmin
        if xmax is not None:
            args_data['xmax'] = xmax

        try:
            # Save xmin and xmax, as they will be overwritten in plot_data
            try:
                xmin = np.min(self._xdata)
                xmax = np.max(self._xdata)
            except ValueError:
                xmin = None
                xmax = None

            if 'color' not in args_data:
                args_data['color'] = 'black'

            self.plot_data(ylog = ylog, **args_data)

            if ranges is None:
                if 'xmin' not in args_func:
                    args_func['xmin'] = xmin

                if 'xmax' not in args_func:
                    args_func['xmax'] = xmax

                self.plot_func(params, params_err, no_error = no_error, **args_func)
            else:
                cmap = mpl.cm.jet
                for i, val in enumerate(ranges):
                    col = cmap(0.9 * i / max((float(len(ranges)) - 1), 1))
                    if params_err is None:
                        self.plot_func(params[i], xmin = val[0], xmax = val[1], color = col, no_error = no_error, **args_func)
                    else:
                        self.plot_func(params[i], params_err[i], xmin = val[0], xmax = val[1], color = col,
                                       no_error = no_error, **args_func)


        except Exception as e:
            logger.warn("Plotting of fit failed: ", e)


    def plot_cor(self, title = 'Data correlation matrix', xlabel='$x_j$', ylabel='$x_i$'):
        """ Plot the correlation matrix of the input data. """

        ncov = self._fit_cor

        if title is not None:
            plt.title(title)

        plt.xlabel(xlabel)
        plt.ylabel(ylabel)

        xrange = np.arange(len(ncov) + 1)
        yrange = np.arange(len(ncov) + 1)

        plt.pcolormesh(xrange, yrange, ncov, cmap="Blues")
        plt.gca().invert_yaxis()
        cb = plt.colorbar()
        cb.ax.tick_params(labelsize=12)


    def plot_eig(self, xlabel="$i$", ylabel="$\\lambda_i$", title = 'Eigenvalues of data correlation matrix'):

        vals, _ = np.linalg.eig(self._fit_cor)

        eig_real = np.real(vals)
        eig_imag = np.imag(vals)

        plot_bar(range(len(eig_real)), eig_real, color='#d32d11', label="real", alpha=0.7, title=title, xlabel=xlabel, ylabel=ylabel)
        if np.min(eig_imag) != 0:
            plot_bar(range(len(eig_imag)), eig_imag, color='#0081bf', label="imag", alpha=0.7, title=title, xlabel=xlabel, ylabel=ylabel)


    def get_func(self, x, params = None, params_err = None):
        """ Get value and error of the fit at a specific point x

        Parameters
        ----------
        x: scalar
            x-value at which the function is evaluated.
        params: array_like, optional, default = None
            parameters for the function.
        params_err: array_like, optional, default = None
            error of the parameters for the function.
        """

        if params_err is None and params is None:
            params_err = self._saved_pcov
        if params is None:
            params = self._saved_params

        if params_err is None:
            raise ValueError("Please pass params along with params_err")

        value = self.wrap_func(x, params)

        func = lambda x, *params: self.wrap_func(x, params)
        grad = lambda x, *params: self.grad(x, params)
        try:
            error = [error_prop_func(xval, func, params, params_err, grad = grad) for xval in x]
        except (ValueError, TypeError):
            error = error_prop_func(x, func, params, params_err, grad = grad)

        return value, error




def save_func(func, filename, args=(), func_err=None, args_err=(), grad = None, header=None, **params):
    fill_param_dict(params)
    xmin = params['xmin']
    xmax = params['xmax']

    if params['expand']:
        wrap_func = lambda x, *wrap_args: func(x, *wrap_args)
        wrap_func_err = lambda x, *wrap_args_err: func_err(x, *wrap_args_err)
        wrap_grad = lambda x, *wrap_args: grad(x, *wrap_args)
    else:
        wrap_func = lambda x, *wrap_args: func(x, wrap_args)
        wrap_func_err = lambda x, *wrap_args_err: func_err(x, wrap_args_err)
        wrap_grad = lambda x, *wrap_args: grad(x, wrap_args)

    if xmin is None:
        for line in plt.gca().lines:
            xmin_new = np.min(line.get_xdata())
            if xmin is None:
                xmin = xmin_new
            if xmin_new < xmin:
                xmin = xmin_new
    if xmax is None:
        for line in plt.gca().lines:
            xmax_new = np.max(line.get_xdata())
            if xmax is None:
                xmax = xmax_new
            if xmax_new > xmax:
                xmax = xmax_new

    if xmin is None:
        xmin = -10
    if xmax is None:
        xmax = 10

    xdata = np.arange(xmin, xmax, (xmax - xmin) / params['npoints'])
    ydata = wrap_func(xdata, *args)

    if func_err is not None:
        ydata_err = wrap_func_err(xdata, *args_err)

        with open(filename, "w") as fout:
            for i in range(len(xdata)):
                print(xdata[i], ydata[i], ydata_err[i], file = fout)

    elif len(args_err) > 0:
        if grad is None:
            logger.warn("Used numerical derivative!")
            wrap_grad = None

        # Arguments that are part of the error propagation
        tmp_args = tuple(args)[0:len(args_err)]

        # Optional arguments that are constant and, therefore, not part of the error propagation
        tmp_opt = tuple(args)[len(args_err):]

        ydata_err = error_prop_func(xdata, wrap_func, tmp_args, args_err, grad = wrap_grad, args = tmp_opt)

        writeTable(filename,xdata,ydata,ydata_err,header=header)

    else:
        writeTable(filename,xdata,ydata,header=header)


def do_fit(func, xdata, ydata, edata = None, start_params = None, priorval = None, priorsigma = None,
           algorithm = "curve_fit", detailedInfo=False, **kwargs):
    """ Wrapper to fitter initialization and the fit in one step. See above for arguments. """
    fit = Fitter(func, xdata, ydata, edata, **kwargs)
    return fit.do_fit(start_params = start_params, priorval = priorval, priorsigma = priorsigma, algorithm = algorithm,
                      detailedInfo=detailedInfo)


def try_fit(func, xdata, ydata, edata = None, start_params = None, priorval = None, priorsigma = None,
            algorithms = None, detailedInfo=False, **kwargs):
    """ Wrapper to fitter initialization and the fit in one step. See above for arguments. For historical reasons
    algorithms has no default values here. """
    fit = Fitter(func, xdata, ydata, edata, **kwargs)
    return fit.try_fit(algorithms, start_params, priorval, priorsigma, detailedInfo=detailedInfo)


