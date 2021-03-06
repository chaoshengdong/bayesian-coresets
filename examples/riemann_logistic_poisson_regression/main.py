from __future__ import print_function
import numpy as np
import bayesiancoresets as bc
from scipy.optimize import minimize
import time
from scipy.optimize import nnls
import os, sys

#make it so we can import models/etc from parent folder
sys.path.insert(1, os.path.join(sys.path[0], '../common'))
from mcmc import sampler
import gaussian

#computes the Laplace approximation N(mu, Sig) to the posterior with weights wts
def get_laplace(wts, Z, mu0):
  trials = 10
  Zw = Z[wts>0, :]
  ww = wts[wts>0]
  while True:
    try:
      res = minimize(lambda mu : -log_joint(Zw, mu, ww), mu0, jac=lambda mu : -grad_log_joint(Zw, mu, ww))
    except:
      mu0 = mu0.copy()
      mu0 += np.sqrt((mu0**2).sum())*0.1*np.random.randn(mu0.shape[0])
      trials -= 1
      if trials <= 0:
        print('Tried laplace opt 10 times, failed')
        break
      continue
    break
  mu = res.x
  Sig = -np.linalg.inv(hess_log_joint_w(Zw, mu, ww))
  return mu, Sig

dnm = sys.argv[1] #should be synth_lr / phishing / ds1 / synth_poiss / biketrips / airportdelays
alg = sys.argv[2] #should be GIGAO / GIGAR / RAND / PRIOR / SVI
ID = sys.argv[3] #just a number to denote trial #, any nonnegative integer

np.random.seed(int(ID))

#load the logistic or poisson regression model depending on selected folder
tuning = {'synth_lr': (50, lambda itr : 1./(1.+itr)**0.5), 
          'synth_lr_large': (50, lambda itr : 1./(1.+itr)**0.5), 
          'ds1': (50, lambda itr : 1./(1.+itr)**0.5), 
          'ds1_large': (50, lambda itr : 1./(1.+itr)**0.5), 
          'phishing': (50, lambda itr : 1./(1.+itr)**0.5), 
          'phishing_large': (50, lambda itr : 1./(1.+itr)**0.5), 
          'synth_poiss': (50, lambda itr : 1./(1.+itr)**0.5), 
          'synth_poiss_large': (50, lambda itr : 1./(1.+itr)**0.5), 
          'biketrips': (200, lambda itr : 1./(1.+itr)**0.5), 
          'biketrips_large': (200, lambda itr : 1./(1.+itr)**0.5), 
          'airportdelays': (200, lambda itr : 1./(1.+itr)**0.5),
          'airportdelays_large': (200, lambda itr : 1./(1.+itr)**0.5)}

lrdnms = ['synth_lr', 'phishing', 'ds1', 'synth_lr_large', 'phishing_large', 'ds1_large']
prdnms = ['synth_poiss', 'biketrips', 'airportdelays', 'synth_poiss_large', 'biketrips_large', 'airportdelays_large']
if dnm in lrdnms:
  from model_lr import *
else:
  from model_poiss import *

print('running ' + str(dnm)+ ' ' + str(alg)+ ' ' + str(ID))

if not os.path.exists('results/'):
  os.mkdir('results')

if not os.path.exists('results/'+dnm+'_samples.npy'):
  print('No MCMC samples found -- running STAN')
  #run sampler
  N_samples = 10000
  sampler(dnm, dnm in lrdnms, '../data/', 'results/', N_samples)


print('Loading dataset '+dnm)
Z, Zt, D = load_data('../data/'+dnm+'.npz')
print('Loading posterior samples for '+dnm)
samples = np.load('results/'+dnm+'_samples.npy')
#TODO FIX SAMPLER TO NOT HAVE TO DO THIS
samples = np.hstack((samples[:, 1:], samples[:, 0][:,np.newaxis]))

#fit a gaussian to the posterior samples 
#used for pihat computation for Hilbert coresets with noise to simulate uncertainty in a good pihat
mup = samples.mean(axis=0)
Sigp = np.cov(samples, rowvar=False)

#create the prior -- also used for the above purpose
mu0 = np.zeros(mup.shape[0])
Sig0 = np.eye(mup.shape[0])

###############################
## TUNING PARAMETERS ##
#Ms = [1, 2, 5, 10, 20, 50, 100, 200, 499] #coreset sizes at which we record output
learning_rate = lambda itr : 0.5/(1.+itr)
n_samples = 100
M = 100
projection_dim = 100 #random projection dimension for Hilbert csts
pihat_noise = .75 #noise level (relative) for corrupting pihat
opt_itrs = 500
###############################

#get pihat via interpolation between prior/posterior + noise
#uniformly smooth between prior and posterior
U = np.random.rand()
muhat = U*mup + (1.-U)*mu0
Sighat = U*Sigp + (1.-U)*Sig0
#now corrupt the smoothed pihat
muhat += pihat_noise*np.sqrt((muhat**2).sum())*np.random.randn(muhat.shape[0])
Sighat *= np.exp(-2.*pihat_noise*np.fabs(np.random.randn()))

print('Building tangent space factories')
#build tangent spaces
tsf_optimal = bc.BayesianTangentSpaceFactory(lambda th : log_likelihood_2d2d(Z, th), lambda sz, wts, idcs : np.random.multivariate_normal(mup, Sigp, sz), projection_dim)
tsf_realistic = bc.BayesianTangentSpaceFactory(lambda th : log_likelihood_2d2d(Z, th), lambda sz, wts, idcs : np.random.multivariate_normal(muhat, Sighat, sz), projection_dim)

def sampler_w(sz, wts, idcs):
  if idcs.shape[0] > 0:
    w = np.zeros(Z.shape[0])
    w[idcs] = wts
    muw, Sigw = get_laplace(w, Z, mu0)
  else:
    muw, Sigw = mu0, Sig0
  return np.random.multivariate_normal(muw, Sigw, sz)
tsf_w = bc.BayesianTangentSpaceFactory(lambda th : log_likelihood_2d2d(Z, th), sampler_w, projection_dim)
 
print('Creating coresets object')
#create coreset construction objects
giga_optimal = bc.HilbertCoreset(tsf_optimal)
giga_realistic = bc.HilbertCoreset(tsf_realistic)
unif = bc.UniformSamplingCoreset(Z.shape[0])
sparsevi = bc.SparseVICoreset(tsf_w, opt_itrs=opt_itrs, step_sched = learning_rate)

algs = {'SVI': sparsevi, 
        'GIGAO': giga_optimal, 
        'GIGAR': giga_realistic, 
        'RAND': unif,
        'PRIOR': None}
coreset = algs[alg]

print('Building coresets via ' + alg)
#build
wts = np.zeros((M+1, Z.shape[0]))
cputs = np.zeros(M+1)
t0 = time.perf_counter()
for m in range(1, M+1):
  print(str(m)+'/'+str(M))
  if alg != 'PRIOR':
    coreset.build(1, m)

    #record time and weights
    cputs[m] = time.perf_counter()-t0
    w, idcs = coreset.weights()
    wts[m, idcs] = w
    
#get laplace approximations for each weight setting, and KL divergence to full posterior laplace approx mup Sigp
#used for a quick/dirty performance comparison without expensive posterior sample comparisons (e.g. energy distance)
mus_laplace = np.zeros((M+1, D))
Sigs_laplace = np.zeros((M+1, D, D))
kls_laplace = np.zeros(M+1)
print('Computing coreset Laplace approximation + approximate KL(posterior || coreset laplace)')
for m in range(M+1):
  mul, Sigl = get_laplace(wts[m,:], Z, Z.mean(axis=0)[:D])
  mus_laplace[m,:] = mul
  Sigs_laplace[m,:,:] = Sigl
  kls_laplace[m] = gaussian.gaussian_KL(mup, Sigp, mul, np.linalg.inv(Sigl))

#save results
np.savez('results/'+dnm+'_'+alg+'_results_'+str(ID)+'.npz', cputs=cputs, wts=wts, Ms=np.arange(M+1), mus=mus_laplace, Sigs=Sigs_laplace, kls=kls_laplace)


