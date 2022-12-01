# AUTOGENERATED! DO NOT EDIT! File to edit: ../03_models.ipynb.

# %% auto 0
__all__ = ['ToyODE', 'make_model', 'Autoencoder', 'ToyModel', 'ToySDEModel']

# %% ../03_models.ipynb 3
import itertools
from torch.nn  import functional as F 
import torch.nn as nn
import torch
class ToyODE(nn.Module):
    """ 
    ODE derivative network
    
    feature_dims (int) default '5': dimension of the inputs, either in ambient space or embedded space.
    layer (list of int) defaulf ''[64]'': the hidden layers of the network.
    activation (torch.nn) default '"ReLU"': activation function applied in between layers.
    scales (NoneType|list of float) default 'None': the initial scale for the noise in the trajectories. One scale per bin, add more if using an adaptative ODE solver.
    n_aug (int) default '1': number of added dimensions to the input of the network. Total dimensions are features_dim + 1 (time) + n_aug. 
    
    Method
    forward (Callable)
        forward pass of the ODE derivative network.
        Parameters:
        t (torch.tensor): time of the evaluation.
        x (torch.tensor): position of the evalutation.
        Return:
        derivative at time t and position x.   
    """
    def __init__(
        self, 
        feature_dims=5,
        layers=[64],
        activation='ReLU',
        scales=None,
        n_aug=2
    ):
        super(ToyODE, self).__init__()
        steps = [feature_dims+1+n_aug, *layers, feature_dims]
        pairs = zip(steps, steps[1:])

        chain = list(itertools.chain(*list(zip(
            map(lambda e: nn.Linear(*e), pairs), 
            itertools.repeat(getattr(nn, activation)())
        ))))[:-1]

        self.chain = chain
        self.seq = (nn.Sequential(*chain))
        
        self.alpha = nn.Parameter(torch.tensor(scales, requires_grad=True).float()) if scales is not None else None
        self.n_aug = n_aug        
        
    def forward(self, t, x): #NOTE the forward pass when we use torchdiffeq must be forward(self,t,x)
        zero = torch.tensor([0]).cuda() if x.is_cuda else torch.tensor([0])
        zeros = zero.repeat(x.size()[0],self.n_aug)
        time = t.repeat(x.size()[0],1)
        aug = torch.cat((x,time,zeros),dim=1)
        x = self.seq(aug)
        if self.alpha is not None:
            z = torch.randn(x.size(),requires_grad=False).cuda() if x.is_cuda else torch.randn(x.size(),requires_grad=False)
        dxdt = x + z*self.alpha[int(t-1)] if self.alpha is not None else x
        return dxdt

# %% ../03_models.ipynb 4
def make_model(
    feature_dims=5,
    layers=[64],
    output_dims=5,
    activation='ReLU',
    which='ode',
    method='rk4',
    rtol=None,
    atol=None,
    scales=None,
    n_aug=2,
    noise_type='diagonal', sde_type='ito',
    use_norm=False,
    use_cuda=False,
    in_features=2, out_features=2, gunc=None
):
    """
    Creates the 'ode' model or 'sde' model or the Geodesic Autoencoder. 
    See the parameters of the respective classes.
    """
    if which == 'ode':
        ode = ToyODE(feature_dims, layers, activation,scales,n_aug)
        model = ToyModel(ode,method,rtol, atol, use_norm=use_norm)
    elif which == 'sde':
        ode = ToyODE(feature_dims, layers, activation,scales,n_aug)
        model = ToySDEModel(
            ode, method, noise_type, sde_type,
            in_features=in_features, out_features=out_features, gunc=gunc
            
        )
    else:
        model = ToyGeo(feature_dims, layers, output_dims, activation)  # FIXME cannot find `ToyGeo`
    if use_cuda:
        model.cuda()
    return model 

# %% ../03_models.ipynb 5
import itertools
import torch.nn as nn
from torch.nn  import functional as F 

class Autoencoder(nn.Module):
    """ 
    Geodesic Autoencoder
    
    encoder_layers (list of int) default '[100, 100, 20]': encoder_layers[0] is the feature dimension, and encoder_layers[-1] the embedded dimension.
    decoder_layers (list of int) defaulf '[20, 100, 100]': decoder_layers[0] is the embbeded dim and decoder_layers[-1] the feature dim.
    activation (torch.nn) default '"Tanh"': activation function applied in between layers.
    use_cuda (bool) default to False: Whether to use GPU or CPU.
    
    Method
    encode
        forward pass of the encoder
        x (torch.tensor): observations
        Return:
        the encoded observations
    decode
        forward pass of the decoder
        z (torch.tensor): embedded observations
        Return:
        the decoded observations
    forward (Callable):
        full forward pass, encoder and decoder
        x (torch.tensor): observations
        Return:
        denoised observations
    """

    def __init__(
        self,
        encoder_layers = [100, 100, 20],
        decoder_layers = [20, 100, 100],
        activation = 'Tanh',
        use_cuda = False
    ):        
        super(Autoencoder, self).__init__()
        if decoder_layers is None:
            decoder_layers = [*encoder_layers[::-1]]
        device = 'cuda' if use_cuda else 'cpu'
        
        encoder_shapes = list(zip(encoder_layers, encoder_layers[1:]))
        decoder_shapes = list(zip(decoder_layers, decoder_layers[1:]))
        
        encoder_linear = list(map(lambda a: nn.Linear(*a), encoder_shapes))
        decoder_linear = list(map(lambda a: nn.Linear(*a), decoder_shapes))
        
        encoder_riffle = list(itertools.chain(*zip(encoder_linear, itertools.repeat(getattr(nn, activation)()))))[:-1]
        encoder = nn.Sequential(*encoder_riffle).to(device)
        
        decoder_riffle = list(itertools.chain(*zip(decoder_linear, itertools.repeat(getattr(nn, activation)()))))[:-1]

        decoder = nn.Sequential(*decoder_riffle).to(device)
        self.encoder = encoder
        self.decoder = decoder

        
    
    def encode(self, x):
        return self.encoder(x)

    def decode(self, z):
        return self.decoder(z)

    def forward(self, x):
        z = self.encoder(x)
        return self.decoder(z)

# %% ../03_models.ipynb 6
from torchdiffeq import odeint_adjoint as odeint
import os, math, numpy as np
import torch
import torch.nn as nn
class ToyModel(nn.Module):
    """ 
    Neural ODE
        func (nn.Module): The network modeling the derivative.
        method (str) defaulf '"rk4"': any methods from torchdiffeq.
        rtol (NoneType | float): the relative tolerance of the ODE solver.
        atol (NoneType | float): the absolute tolerance. of the ODE solver.
        use_norm (bool): if True keeps the norm of func.
        norm (list of torch.tensor): the norm of the derivative.
        
        Method
        forward (Callable)
            x (torch.tensor): the initial sample
            t (torch.tensor) time points where we suppose x is from t[0]
            return the last sample or the whole seq.      
    """
    
    def __init__(self, func, method='rk4', rtol=None, atol=None, use_norm=False):
        super(ToyModel, self).__init__()        
        self.func = func
        self.method = method
        self.rtol=rtol
        self.atol=atol
        self.use_norm = use_norm
        self.norm=[]

    def forward(self, x, t, return_whole_sequence=False):

        if self.use_norm:
            for time in t: 
                self.norm.append(torch.linalg.norm(self.func(time,x)).pow(2))
        if self.atol is None and self.rtol is None:
            x = odeint(self.func,x ,t, method=self.method)
        elif self.atol is not None and self.rtol is None:
            x = odeint(self.func,x ,t, method=self.method, atol=self.atol)
        elif self.atol is None and self.rtol is not None:
            x = odeint(self.func,x ,t, method=self.method, rtol=self.rtol)
        else: 
            x = odeint(self.func,x ,t, method=self.method, atol=self.atol, rtol=self.rtol)          
       
        x = x[-1] if not return_whole_sequence else x
        return x

# %% ../03_models.ipynb 7
from torchdiffeq import odeint_adjoint as odeint
import os, math, numpy as np
import torch
import torch.nn as nn
import torchsde

class ToySDEModel(nn.Module):
    """ 
    Neural SDE model
        func (nn.Module): drift term.
        genc (nn.Module): diffusion term.
        method (str): method of the SDE solver.
        
        Method
        forward (Callable)
            x (torch.tensor): the initial sample
            t (torch.tensor) time points where we suppose x is from t[0]
            return the last sample or the whole seq.  
    """
    
    def __init__(self, func, method='euler', noise_type='diagonal', sde_type='ito', 
    in_features=2, out_features=2, gunc=None, dt=0.1):
        super(ToySDEModel, self).__init__()        
        self.func = func
        self.method = method
        self.noise_type = noise_type
        self.sde_type = sde_type
        if gunc is None:
            self._gunc_args = 'y'
            self.gunc = nn.Linear(in_features, out_features)
        else:
            self._gunc_args = 't,y'
            self.gunc = gunc

        self.dt = dt
        
    def f(self, t, y):
        return self.func(t, y)

    def g(self, t, y):
        return self.gunc(t, y) if self._gunc_args == 't,y' else self.gunc(y)
        return 0.3 * torch.sigmoid(torch.cos(t) * torch.exp(-y))

    def forward(self, x, t, return_whole_sequence=False, dt=None):
        dt = self.dt if self.dt is not None else 0.1 if dt is None else dt        
        x = torchsde.sdeint(self, x, t, method=self.method, dt=dt)
       
        x = x[-1] if not return_whole_sequence else x
        return x
