# 
# plot_random.py                                                               
# 
# H. Sandmeyer
# 
# Quick plot of a normal distribution.
#

from numpy.random import normal
from latqcdtools.base.plotting import latexify, set_default_param, plot_dots, set_params, plt

latexify()

for i in range(8):
    set_default_param(point_fill_color = "None")
    xdata = normal(1, 0.1, 10)
    ydata = 3*i + normal(1, 0.1, 10)
    edata = normal(1, 0.01, 10)
    plot_dots(xdata, ydata, edata, label = str(i))
    set_params(legendpos = (1,0))

plt.show()

