import numpy as np
import mpmath
import latqcdtools.autocorrelation as autocorr
import latqcdtools.tools as tools
import latqcdtools.logger as logger
from latqcdtools.num_deriv import diff_jac


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
def std_mean(data, axis = 0):
    return np.mean(data, axis)


@reduce_tuple
def std_median(data, axis = 0):
    return np.median(data, axis)


@reduce_tuple
def std_err(data, axis = 0):
    data = np.asarray(data)
    return std_dev(data, axis) / np.sqrt(data.shape[axis])


@reduce_tuple
def dev_by_dist(data, axis = 0):
    """Calculate the distance between the median and 68% quantiles. Returns the larger of the two distances. This
    method is used sometimes to estimate error."""
    data = np.asarray(data)
    data = np.rollaxis(data, 0, axis + 1)
    median = np.median(data, 0)
    numb_data = data.shape[0]
    idx_dn = int(numb_data / 2 - 0.68 * numb_data / 2)
    idx_up = int(numb_data / 2 + 0.68 * numb_data / 2)
    sorted_data = np.sort(data - median, axis = 0)
    return np.max(np.abs([sorted_data[idx_dn], sorted_data[idx_up]]), axis = 0)


@reduce_tuple
def std_var(data, axis = 0):
    data = np.asarray(data)
    return np.var(data, axis = axis, ddof = 1)


@reduce_tuple
def std_dev(data, axis = 0):
    data = np.asarray(data)
    return np.std(data, axis = axis, ddof = 1)


def mean_and_err(data, axis = 0):
    mean = std_mean(data, axis = axis)
    error = std_err(data, axis = axis)
    return mean, error


def mean_and_cov(data, axis = 0):
    mean = std_mean(data, axis = axis)
    cov = calc_cov(data)
    return mean, cov


def mean_and_std_dev(data, axis=0):
    mean = std_mean(data, axis = axis)
    std = std_dev(data, axis = axis)
    return mean, std


def calc_cov(data):
    """Calculate covariance matrix of last column in data."""
    data = np.asarray(data)
    mean = np.mean(data, axis=0)

    newshape = np.hstack((data.shape[1:], data.shape[-1]))
    res = np.zeros(newshape)
    
    for l in range(0, len(data)):
        diff = data[l] - mean
        tmp = np.einsum('...j,...k->...jk', diff, diff)
        res += tmp

    return (1 / (len(data) - 1)) * res


def norm_cov(cov):
    """Normalize a covariance matrix to create the correlation matrix."""
    res = np.zeros((len(cov), len(cov[0])))
    for i in range(len(cov)):
        for j in range(len(cov[0])):
            res[i][j] = cov[i][j] / np.sqrt((cov[j][j] * cov[i][i]))
    return np.array(res)


def rem_norm_corr(corr, edata):
    """Compute the covariance matrix from a correlation matrix."""
    res = np.zeros((len(corr), len(corr[0])))
    for i in range(len(corr)):
        for j in range(len(corr[0])):
            res[i][j] = corr[i][j] * edata[i]*edata[j]
    return np.array(res)



def error_prop(func, means, errors, grad=None, use_diff = True, args=()):
    mean = func(means, *args)
    errors = np.asarray(errors)
    try:
        # Test if we got a covariance matrix
        errors[0][0]
    except:
        errors = np.diag(errors ** 2)
    if type(mean) is tuple:
        raise TypeError("Tuples are not supported for error propagation")

    if grad is not None:
        grad = grad(means, *args)
    else:
        if use_diff:
            grad = diff_jac(means, func, args).transpose()
        else:
            grad = tools.alg_jac(means, func, args).transpose()
    error = 0
    try:
        for i in range(len(grad)):
            for j in range(len(grad)):
                error += grad[i] * grad[j] * errors[i, j]
        error = np.sqrt(error)
    except TypeError:
        error += abs(grad * errors[0])
    return mean, error



# Function to calculate error propagation for plotting
def error_prop_func(x, func, means, errors, grad=None, use_diff = True, args=()):
    # For fitting or plotting we expect the first argument of func to be x instead of params.
    # Therefore we have to change the order using this wrapper
    wrap_func = lambda p, *args: func(x, *(tuple(p) + tuple(args)))
    if grad is not None:
        wrap_grad = lambda p, *grad_args: grad(x, *(tuple(p) + tuple(grad_args)))
    else:
        wrap_grad = None
    return error_prop(wrap_func, means, errors, wrap_grad, use_diff, args)[1]




def aicc_av(aicc, data, errors = None):
    """Weighted average using AICc as weights"""
    data = np.asarray(data)
    errors = np.asarray(errors)
    if errors is None:
        errors = np.ones_like(data)
    aicc = np.asarray(aicc)
    aicc_min = np.min(aicc)
    daicc = aicc - aicc_min
    weights = np.exp(-0.5 * daicc)
    weights /= np.sum(weights)
    return np.sum(weights * data), np.sqrt(np.sum(weights ** 2 * errors ** 2))



def compute_autocorr_impr_err(data, tmax):
    t_arr, corr_arr, corr_err_arr, int_corr_arr, int_corr_err_arr = autocorr.corr(
            data, tmax=tmax, startvalue=0)

    N = len(data)
    err_sqrt = (np.var(data) / N) * (2 * int_corr_arr[tmax - 1])
    return np.sqrt(err_sqrt)


def compute_autocorr_impr_err_arr(data, tmax):
    I=len(data[0])
    K=len(data[0][0])
    err=np.zeros((I, K))
    mean = np.mean(data, axis=0)
    for i in range(len(data[0])):
        print("Column: ",i)
        for k in range(len(data[0][0])):
            err[i,k]=autocorr.compute_autocorr_impr_err(data[:, i, k], tmax)
    return np.transpose(mean), np.transpose(err)


''' Some of the following are adapted from software of Bernd Berg, Markov Chain Monte Carlo and their Statistical 
    Analysis, World Scientific, 2004. ISBN: 978-981-3106-37-6. '''


def gaudif(x1,e1,x2,e2):
    """Likelihood that observed difference between two estimates is due to chance, assuming that both estimates are
    normally distributed with the same mean.

     INPUT:
      xm1,2--Estimates of some mean.
      eb1,2--Associated error bars.

     OUTPUT:
          q--q value."""
    sigma=np.sqrt(e1**2 + e2**2)
    x=abs(x1-x2)/(sigma * np.sqrt(2.))
    q= 1.0 - mpmath.erf(x)
    return q



def jackknifeFrom(x):
    """Create jackknife data from float list. The number of bins is the number of data. Adapted from Berg.

    INPUT:
       x--List of data.

    OUTPUT:
      xj--List of jackknife data."""
    x=np.array(x)
    ndat=len(x)
    if ndat<2:
        logger.TBError("Need n>1.")
    xj=1.0/(ndat-1.0)*(np.sum(x)-x)
    return xj


def tauint(nt,ts,xhat=None):
    """Given a time series, calculate estimators for its integrated autocorrelation time  at each Markov time
    separation. Adapted from Berg.

    INPUT:
         nt--The largest you think tau_int could be.
         ts--Time series array of measurments. Must be taken from equilibrium ensemble so that
             time translation invariance holds. List must be in order of Markov chain generation.
       xhat--True mean of time series (if you know it).

    OUTPUT:
      acint--List of integrated autocorrelation times."""
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
        acint.append(acint[it-1]+2.*acov[it]/acov[0])
    return acint


def tauintj(nt,nbins,ts,xhat=None):
    """Given a time series, calculate jackknife bins of integrated autocorrelation time for each Markov time separation.
    Adapted from Berg.

    INPUT:
          nt--The largest you think tau_int could be.
       nbins--The number of jackknife bins.
          ts--Time series array of measurements. Must be taken from equilibrium ensemble so that
              time translation invariance holds. List must be in order of markov chain generation
        xhat--True mean of time series (if you know it).

    OUTPUT:
      acintj--2D list indexed by time, then bin number acintj[it][ibin]"""
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


def getTauInt(ts, nbins, tpickMax, acoutfileName='acor.d'):

    acoutfile=open(acoutfileName,'w')

    # Get integrated autocorrelation time list and corresponding jackknife list.
    acint  = np.array( tauint (tpickMax,ts,) )
    acintj = np.array( tauintj(tpickMax,nbins,ts) )

    # This block outputs time, tau_int, tau_int error and bias, then gives an estimate of tau_int. When tau_int
    # decreases for the first time, we call that our estimate. This is because we know tau_int should be a monotonically
    # increasing function of t.
    lmonoton=True
    tau_int=0.
    for it in range(tpickMax+1):
        n=len(acintj[it])
        acm=np.mean(acintj[it])
        ace=np.sqrt( np.sum((acintj[it]-acm)**2)*(n-1)/n )
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

    return tau_int, tau_inte, tau_intbias, itpick