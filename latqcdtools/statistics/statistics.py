#
# statistics.py
#
# H. Sandmeyer, D. Clarke
#
# A collection of basic methods for statistical analysis. The following methods have been adapted from software of
# Bernd Berg, Markov Chain Monte Carlo and their Statistical Analysis, World Scientific, 2004, ISBN=978-981-3106-37-6:
#     gaudif, jackknifeFrom, tauint, tauintj, getTauInt
#
import mpmath
import numpy as np
import matplotlib.pyplot as plt
import latqcdtools.base.logger as logger
import latqcdtools.math.num_deriv as numDeriv
from latqcdtools.base.plotting import fill_param_dict, plot_fill, plot_lines, clearPlot, plot_file
from latqcdtools.base.utilities import envector
from latqcdtools.base.printErrorBars import get_err_str


def reduce_tuple(func):
    def func_wrapper(data, *args, **kwargs):
        if type(data[0]) is tuple:
            retvalue = ()
            for i in range(len(data[0])):
                obj_array = []
                for k in range(len(data)):
                    obj_array.append(data[k][i])
                retvalue += (func(obj_array, *args, **kwargs),)
            return retvalue
        else:
            return func(data, *args, **kwargs)
    return func_wrapper


@reduce_tuple
def std_median(data, axis = 0):
    return np.median(data, axis)


@reduce_tuple
def std_mean(data, axis = 0):
    return np.mean(data, axis)


@reduce_tuple
def std_var(data, axis = 0):
    """ Calculate unbiased (ddof = 1) estimator for the variance. """
    data = np.asarray(data)
    return np.var(data, axis = axis, ddof = 1)


@reduce_tuple
def std_dev(data, axis = 0):
    """ Calculate unbiased (ddof = 1) estimator for the standard deviation. """
    data = np.asarray(data)
    return np.std(data, axis = axis, ddof = 1)


@reduce_tuple
def std_err(data, axis = 0):
    """ Standard deviation of the sample mean, according the the CLT. """
    data = np.asarray(data)
    return std_dev(data, axis) / np.sqrt(data.shape[axis])


# TODO: should this be true or false?
def std_cov(data):
    """ If data is a table indexed by observable and measurement number, calculate covariance between observables. """
    data = np.asarray(data)
    if not data.ndim==2:
        logger.TBError('Expected 2d table.')
    return np.cov( data, bias=True )


def jack_mean_and_err(data):
    data   = np.asarray(data)
    n      = len(data)
    mean   = np.mean(data)
    err    = np.sqrt( (n-1)*np.mean((data-mean)**2) )
    return mean, err


def weighted_mean(data, weights):
    """ Compute the weighted mean. """
    data    = np.asarray(data)
    weights = np.asarray(weights)
    return np.sum(data.dot(weights))/np.sum(weights)


#https://mathoverflow.net/questions/11803/unbiased-estimate-of-the-variance-of-a-weighted-mean
#In above source, the weights are normalized. We normalize like Wikipedia
#https://en.wikipedia.org/wiki/Weighted_arithmetic_mean#Weighted_sample_variance


def weighted_mean_variance(errors, weights = None):
    """ Compute the variance of the weighted mean based on error propagation. This is only valid if the error bars of
    the data are of reasonable size, meaning that most of the error bars include the mean value. If you expect that
    there are unknown systematic errors, you should use unbiased_mean_variance instead.

    Parameters
    ----------

    errors: array_like
        The errors of the data points.
    weights: array_like, optional, default = None
        If you do not use weights = 1/errors**2, you can pass additional weights.
        If None, weights = computed as 1/errors**2. """
    errors = np.asarray(errors)
    if weights is None:
        weights = 1 / errors**2
    return np.sum(weights**2 * errors**2) / np.sum(weights)**2


def biased_sample_variance(data, weights):
    """ Compute the biased weighted sample variance, i.e. the biased variance of an individual measurement and not the
    variance of the mean. """
    mean = weighted_mean(data, weights)
    V1 = np.sum(weights)
    return weights.dot((data - mean)**2) / V1


def unbiased_sample_variance(data, weights):
    """ Compute the unbiased weighted sample variance, i.e. the unbiased variance of an individual measurement and not
    the variance of the mean. Do not use this function if your weights are frequency weights. """
    V1 = np.sum(weights)
    V2 = np.sum(weights**2)
    return biased_sample_variance(data, weights) / ( 1 - (V2 / V1**2))


def unbiased_mean_variance(data, weights):
    """ Compute the unbiased variance of a weighted mean. Do not use this function if your weights are frequency
    weights. This is more like a systematic error. The absolute size of the weights does not matter. The error is
    constructed using the deviations of the individual data points. """
    data    = np.asarray(data)
    weights = np.asarray(weights)
    V1 = np.sum(weights)
    V2 = np.sum(weights**2)
    return biased_sample_variance(data, weights) * V2 / ( V1**2 - V2)


def norm_cov(cov):
    """ Normalize a covariance matrix to create the correlation matrix. """
    res = np.zeros((len(cov), len(cov[0])))
    for i in range(len(cov)):
        for j in range(len(cov[0])):
            res[i][j] = cov[i][j] / np.sqrt((cov[j][j] * cov[i][i]))
    return np.array(res)


@reduce_tuple
def dev_by_dist(data, axis=0, return_both_q=False, percentile=68):
    """ Calculate the distance between the median and 68% quantiles. Returns the larger of the two distances. This
    method is used sometimes to estimate error, for example in the bootstrap. """
    data = np.asarray(data)
    median = np.nanmedian(data, axis)
    numb_data = data.shape[axis]
    idx_dn = max(int(np.floor((numb_data-1) / 2 - percentile/100 * (numb_data-1) / 2)), 0)
    idx_up = min(int(np.ceil((numb_data-1) / 2 + percentile/100 * (numb_data-1) / 2)), numb_data-1)
    sorted_data = np.sort(data - np.expand_dims(median, axis), axis=axis)
    q_l = np.take(sorted_data, idx_dn, axis)
    q_r = np.take(sorted_data, idx_up, axis)
    if return_both_q:
        return np.abs(q_l), np.abs(q_r)
    else:
        return np.max(np.stack((np.abs(q_l), np.abs(q_r)), axis=0), axis=0)


def error_prop(func, means, errors, grad=None, args=()):
    """ Use error propagation to propagate some errors through function func. The function should have the form
        func( data ), where data is your array of input variables. """
    errors = np.asarray(errors)
    means  = np.asarray(means)
    mean   = func(means, *args)
    try:
        # Test if we got a covariance matrix
        errors[0][0]
    except:
        errors = np.diag(errors**2)
    if type(mean) is tuple:
        raise TypeError("Tuples are not supported for error propagation")

    if grad is not None:
        grad = grad(means, *args)
    else:
        grad = numDeriv.diff_jac(means, func, args).transpose()
    error = 0
    try:
        for i in range(len(grad)):
            for j in range(len(grad)):
                error += grad[i] * grad[j] * errors[i, j]
        error = np.sqrt(error)
    except TypeError:
        error += abs(grad * errors[0])
    return mean, error


def error_prop_func(x, func, means, errors, grad=None, args=()):
    """ Automatically wraps your function for error_prop. It only returns the error, not the mean. """
    # For fitting or plotting we expect the first argument of func to be x instead of params.
    # Therefore we have to change the order using this wrapper
    wrap_func = lambda p, *wrap_args: func(x, *(tuple(p) + tuple(wrap_args)))
    if grad is not None:
        wrap_grad = lambda p, *grad_args: grad(x, *(tuple(p) + tuple(grad_args)))
    else:
        wrap_grad = None
    return error_prop(wrap_func, means, errors, wrap_grad, args)[1]


def gaudif(x1,e1,x2,e2):
    """ Likelihood that observed difference between two estimates is due to chance, assuming that both estimates are
    normally distributed with the same mean.

     INPUT:
      xm1,2--Estimates of some mean.
      eb1,2--Associated error bars.

     OUTPUT:
          q--q value. """
    if e1<0 or e2<0:
        logger.TBError('Error bars should be non-negative. Got',e1,e2)
    sigma=np.sqrt(e1**2 + e2**2)
    x=abs(x1-x2)/(sigma * np.sqrt(2.))
    q= 1.0 - mpmath.erf(x)
    return q


def jackknifeFrom(x):
    """ Create jackknife data from float list. The number of bins is the number of data.

    INPUT:
       x--List of data.

    OUTPUT:
      xj--List of jackknife data. """
    x=np.array(x)
    ndat=len(x)
    if ndat<2:
        logger.TBError("Need n>1.")
    xj=1.0/(ndat-1.0)*(np.sum(x)-x)
    return xj


def tauint(nt,ts,xhat = None):
    """ Given a time series, calculate estimators for its integrated autocorrelation time  at each Markov time separation.

    INPUT:
         nt--The largest you think tau_int could be.
         ts--Time series array of measurments. Must be taken from equilibrium ensemble so that
             time translation invariance holds. List must be in order of Markov chain generation.
       xhat--True mean of time series (if you know it).

    OUTPUT:
      acint--List of integrated autocorrelation times. """
    ndat=len(ts)
    if ndat<2:
        logger.TBError("Need ndat>1.")
    if nt>=ndat:
        logger.TBError("Need nt>ndat.")
    if xhat is not None:
        x=xhat
    else:
        x=std_mean(ts)
    # Create array of autocovariance
    acov=[]
    for it in range(nt+1):
        numt=ndat-it # number of pairs of time series elements separated by computer time it
        c_it=0.
        for i in range(numt):
            c_it=c_it+(ts[i]-x)*(ts[i+it]-x)
        c_it=c_it/numt
        # Bias correction. This is needed for c(t) to be correct in the limit of uncorrelated data. In principle
        # you don't know ahead of time whether your raw data are effectively correlated. The factor is ndat rather than
        # ndat-it because ndat data points were used to calculate x, which is what matters for the bias.
        if xhat is None:
            c_it=c_it*ndat/(ndat-1.)
        acov.append(c_it)
    # Calculate integrated autocorrelation time
    acint=[1.]
    for it in range(1,nt+1):
        acint.append( acint[it-1] + 2.*acov[it]/acov[0] )
    return acint


def tauintj(nt,nbins,ts,xhat = None):
    """ Given a time series, calculate jackknife bins of integrated autocorrelation time for each Markov time separation.

    INPUT:
          nt--The largest nt at which you think your estimate for tau_int could lie.
       nbins--The number of jackknife bins.
          ts--Time series array of measurements. Must be taken from equilibrium ensemble so that
              time translation invariance holds. List must be in order of markov chain generation
        xhat--True mean of time series (if you know it).

    OUTPUT:
      acintj--2D list indexed by time, then bin number acintj[it][ibin] """
    ndat=len(ts)
    # The time series ought to have more than one element.
    if ndat<2:
        logger.TBError("Need ndat>1.")
    # And there ought to be at least one jackknife bin.
    if nbins<2:
        logger.TBError("Need nbins>1.")
    if xhat is not None:
        x=xhat
    else:
        x=std_mean(ts)
    # For each it, create a list of nbins jackknife measurements of the autocovariance. The measurements are an average
    # over binsize data with separation it. Everything is stored in a 2D array acorj[it][ibin].
    acorj=[]
    for it in range(nt+1):
        numt=ndat-it
        binsize=int(numt/nbins)
        acor=[0.]*nbins
        # This is a check that we can't spill into the last bin
        itmax=nbins*binsize-binsize
        if it>=itmax:
            logger.TBError("it>=itmax.")
        for ibin in range(nbins):
            i1=ibin*binsize
            i2=(ibin+1)*binsize-1
            for i in range(i1,i2+1):
                acor[ibin]=acor[ibin]+(ts[i]-x)*(ts[i+it]-x)
            acor[ibin]=acor[ibin]/binsize
            # Bias correction
            if xhat is None:
                acor[ibin]=acor[ibin]*ndat/(ndat-1.)
        acorj.append(jackknifeFrom(acor))
    # Now make acintj
    acintj=[[1.]*nbins]
    for it in range(1,nt+1):
        tauintbins=[]
        for ibin in range(nbins):
            tauintbins.append(acintj[it-1][ibin]+2.*acorj[it][ibin]/acorj[0][ibin])
        acintj.append(tauintbins)
    return acintj


def getTauInt(ts, nbins, tpickMax, acoutfileName = 'acor.d', showPlot = False):
    """ Given a time series, return estimates for the integrated autocorrelation time and its error.

    INPUT:
         tpickMax--The largest nt at which you think your estimate for tau_int could lie.
            nbins--The number of jackknife bins (for estimating the error in tau_int)
               ts--Time series array of measurements. Must be taken from equilibrium ensemble so that
                   time translation invariance holds. List must be in order of markov chain generation

    OUTPUT:
          tau_int--Estimate for integrated autocorrelation time.
         tau_inte--Its (jackknife) error bar.
      tau_intbias--Its bias.
           itpick--The Monte Carlo separation at which this method found its estimate for tau_int. """

    acoutfile=open(acoutfileName,'w')

    # Get integrated autocorrelation time list and corresponding jackknife list.
    acint  = np.array( tauint (tpickMax,ts,) )
    acintj = np.array( tauintj(tpickMax,nbins,ts) )

    # This block outputs time, tau_int, tau_int error and bias, then gives an estimate of tau_int. When tau_int
    # decreases for the first time, we call that our estimate. This is because we know tau_int should be a monotonically
    # increasing function of t.
    lmonoton=True
    tau_int=0.
    tau_inte=-1
    tau_intbias=-1
    itpick=-1
    for it in range(tpickMax+1):
        acm, ace = jack_mean_and_err(acintj[it])
        acbias=(nbins-1)*abs(acint[it]-acm)
        acoutfile.write(str(it)+'\t'+str(acint[it])+'\t'+str(ace)+'\t'+str(acbias)+'\n')
        if lmonoton:
            if not acint[it]<tau_int:
                tau_int=acint[it]
                tau_inte=ace
                tau_intbias=acbias
                itpick=it
            else:  # acint[it] < tau_int ==> tau_int decreased
                lmonoton=False
    acoutfile.close()

    if showPlot:
        clearPlot()
        plot_file(acoutfileName, xcol=1, ycol=2, yecol=3, xlabel='conf', ylabel='$\\tau_{\\rm int}$')
        plt.show()

    return tau_int, tau_inte, tau_intbias, itpick



def plot_func(func, args=(), func_err=None, args_err=(), grad = None, func_sup_numpy = False, swapXY=False, **params):
    """ To plot an error band with an explicit error function, use func_err. args_err are all parameters for func_err.
        To use a numerical derivative, just pass the errors of args to args_err. The option swapXY allows for error
        bars in the x-direction instead of the y-direction. """

    fill_param_dict(params)
    params['marker'] = None
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

    if func_sup_numpy:
        ydata = wrap_func(xdata, *args)
    else:
        ydata = np.array([wrap_func(x, *args) for x in xdata])

    if func_err is not None:
        if func_sup_numpy:
            ydata_err = wrap_func_err(xdata, *args_err)
        else:
            ydata_err = np.array([wrap_func_err(x, *args_err) for x in xdata])

        if swapXY:
            return plot_fill(xdata, ydata, yedata=None, xedata=ydata_err, **params)
        else:
            return plot_fill(xdata, ydata, ydata_err, **params)

    elif len(args_err) > 0:
        if grad is None:
            logger.warn("Used numerical derivative!")
            wrap_grad = None

        # Arguments that are part of the error propagation
        tmp_args = tuple(args)[0:len(args_err)]

        # Optional arguments that are constant and, therefore, not part of the error propagation
        tmp_opt = tuple(args)[len(args_err):]

        if func_sup_numpy:
            ydata_err = error_prop_func(xdata, wrap_func, tmp_args, args_err, grad = wrap_grad, args = tmp_opt)
        else:
            ydata_err = np.array([error_prop_func(x, wrap_func, tmp_args, args_err, grad = wrap_grad,
                                                  args = tmp_opt) for x in xdata])

        if swapXY:
            return plot_fill(xdata, ydata, yedata=None, xedata=ydata_err, **params)
        else:
            return plot_fill(xdata, ydata, ydata_err, **params)

    else:
        if swapXY:
            return plot_lines(ydata, xdata, yedata=None, xedata=None, **params)
        else:
            return plot_lines(xdata, ydata, yedata=None, xedata=None, **params)


def gaudif_results(res, res_err, res_true, res_err_true, text = "", qcut=0.05, testMode=True):
    """ Compares element-by-element the results of res with res_true using Gaussian difference test, i.e. it checks
        to see whether res and res_true are statistically compatible. """

    test = True

    res          = envector(res)
    res_true     = envector(res_true)
    res_err      = envector(res_err)
    res_err_true = envector(res_err_true)

    for i in range(len(res)):

        q = gaudif(res[i], res_err[i], res_true[i], res_err_true[i])

        if q < qcut:
            test = False
            resstr     = get_err_str(res[i]     ,res_err[i])
            restruestr = get_err_str(res_true[i],res_err_true[i])
            print("res["+str(i)+"] =",resstr,"!= res_true["+str(i)+"] =",restruestr,'[ q =',round(q,2),']')

    if testMode:
        if test:
            logger.TBPass(text)
        else:
            logger.TBFail(text)
    else:
        logger.info(text)