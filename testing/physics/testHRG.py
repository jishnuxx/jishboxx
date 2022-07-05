# 
# testHRG.py                                                               
# 
# D. Clarke 
# 
# A test for some of the HRG methods. 
#

import numpy as np
from latqcdtools.physics.HRG import HRG,EV_HRG
from latqcdtools.base.check import print_results
from latqcdtools.base.plotting import plot_lines,plot_file,set_params,latexify
from latqcdtools.base.cleanData import excludeAtCol,restrictAtCol
import matplotlib.pyplot as plt

EPSILON = 1e-6

T = np.linspace(130, 179.5, 100)

# QM and PDG HRG files
hadrons,M,Q,B,S,C,g = np.loadtxt("../../latqcdtools/physics/HRGtables/QM_hadron_list_ext_strange_2020.txt",unpack=True,
                                 usecols=(0,1,2,3,4,5,6),dtype="U11,f8,i8,i8,i8,i8,i8")
hadrons1,M1,Q1,B1,S1,C1,g1 = np.loadtxt("../../latqcdtools/physics/HRGtables/PDG_hadron_list_ext_2020.txt",unpack=True,
                                        dtype="U11,f8,i8,i8,i8,i8,i8",usecols=(0,1,2,3,4,5,6,7))

# Spin statistics. w= fermi(-1)/bose(1) statistics. (If baryon number is 1, you have three spin-1/2 constituents, which
# allows only half-integer baryon spins.
w  = np.array([1 if ba==0 else -1 for ba in B])
w1 = np.array([1 if ba==0 else -1 for ba in B1])

QMhrg      = HRG(M,g,w,B,S,Q)
pdghrg     = HRG(M1,g1,w1,B1,S1,Q1)
evhrg      = EV_HRG(M,g,w,B,S,Q)
evpdghrg   = EV_HRG(M1,g1,w1,B1,S1,Q1)
mesons_qm  = HRG(M[np.where(B==0)]  ,g[np.where(B==0)]  ,w[np.where(B==0)]  ,B[np.where(B==0)]  ,S[np.where(B==0)]  ,Q[np.where(B==0)])
mesons_pdg = HRG(M1[np.where(B1==0)],g1[np.where(B1==0)],w1[np.where(B1==0)],B1[np.where(B1==0)],S1[np.where(B1==0)],Q1[np.where(B1==0)])


#
# Test: Calculate chi^200_BQS with b=1 and mu/T=1. Compare against trusted control result.
#
b         = 1
muB_div_T = 1
muB       = muB_div_T * T

chi_QM  = QMhrg.gen_chi(T , B_order=2, Q_order=0, S_order=0, mu_B=muB)
chi_pdg = pdghrg.gen_chi(T, B_order=2, Q_order=0, S_order=0, mu_B=muB)
chi_ev  = evhrg.gen_chi(T, b, 1, B_order=2, Q_order=0, S_order=0) \
          + evhrg.gen_chi(T, b, -1, B_order=2, Q_order=0, S_order=0)
chi_ev1 = evpdghrg.gen_chi(T, b, 1, B_order=2, Q_order=0, S_order=0) \
          + evpdghrg.gen_chi(T, b, -1, B_order=2, Q_order=0, S_order=0)

refT, refPDG, refQM, refEV, refEV1 = np.loadtxt("HRGcontrol/chiBQS_200_muB1.00_b1.00_QMHRG2020_BI.control",unpack=True)

print_results(T      , refT  , prec=EPSILON, text="T check")
print_results(chi_pdg, refPDG, prec=EPSILON, text="chiB2 PDG check")
print_results(chi_QM , refQM , prec=EPSILON, text="chiB2 QM check")
print_results(chi_ev , refEV , prec=EPSILON, text="chiB2 EV check")
print_results(chi_ev1, refEV1, prec=EPSILON, text="chiB2 EV1 check")


#
# Test: Calculate the pressure and compare it with PHYSICAL REVIEW D 90, 094503 (2014). We allow for a 2% error
#       tolerance because I had to fish the paper values out by eye and because we are using an updated resonance list
#       compared to what was available in 2014.
#
refT, ref3p_div_T4 = np.loadtxt("HRGcontrol/2014_3P_div_T4.d",unpack=True)
test3p_div_T4      = 3*pdghrg.pressure(refT,0,0,0)
print_results(ref3p_div_T4, test3p_div_T4, prec=2e-2, text="2014 HotQCD 3p/T^4 check")


#
# Test: Calculate chi^1001_BQSC at muB/T=0. This has not yet been well tested; it is only comparing against
#       a result that this code also generated.
#
muB_div_T = 0
muB       = muB_div_T * T

data = np.loadtxt("../../latqcdtools/physics/HRGtables/hadron_list_ext_strange_charm_2020.txt",unpack=True,
                  usecols=(1,2,3,4,5,6),dtype="f8,i8,i8,i8,i8,i8")

M = data[0]
Q = data[1]
B = data[2]
S = data[3]
C = data[4]
g = data[5]
w = np.array([1 if ba==0 else -1 for ba in B])

QMhrg   = HRG(M,g,w,B,S,Q,C)
chi_QM  = QMhrg.gen_chi(T , B_order=1, Q_order=0, S_order=0, C_order=1, mu_B=muB)
chi_pdg = pdghrg.gen_chi(T, B_order=1, Q_order=0, S_order=0, C_order=1, mu_B=muB)

refT, refPDG, refQM = np.loadtxt("HRGcontrol/chiBQSC_1001_muB0.00_QMHRG2020_BI_charm.control",unpack=True)
print_results(T      , refT  , prec=EPSILON, text="T check")
print_results(chi_pdg, refPDG, prec=EPSILON, text="chiBQSC1001 PDG check")
print_results(chi_QM , refQM , prec=EPSILON, text="chiBQSC1001 QM check")


#
# Test: Compare charm results against Physics Letters B 737 (2014) 210–215. I compare with their QMHRG. The tolerance
#       here is even higher. But I expect only to get the right ballpark, since again we are not using the same states.
#       - Fig. 1: for the pressure
#       - Fig. 4: for the derivatives
#

# First exclude all states that have C = 0.
openCharmStates = excludeAtCol(data,4,0)

# Mesons are those states with B = 0.
openCharmMesons = restrictAtCol(openCharmStates,2,0)

M = openCharmMesons[0]
Q = openCharmMesons[1]
B = openCharmMesons[2]
S = openCharmMesons[3]
C = openCharmMesons[4]
g = openCharmMesons[5]
w = np.array([1 if ba==0 else -1 for ba in B])
QMhrg = HRG(M,g,w,B,S,Q,C)
refT, refMesonP_div_T4 = np.loadtxt("HRGcontrol/2014_P_Mc.d",unpack=True)
mesonP_div_T4 = QMhrg.pressure(refT,0,0,0)
print_results(mesonP_div_T4, refMesonP_div_T4, prec=1.8e-1, text="2014 HotQCD meson open charm")
#latexify()
#plot_lines(T,QMhrg.pressure(T,0,0,0),xmax=175,marker=None)
#plot_file("HRGcontrol/2014_P_Mc.d",style="lines",marker=None,label="HotQCD 2014")
#set_params(xlabel="$T$",ylabel="$P/T^4$")
#plt.show()