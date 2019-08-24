#%% 
import torch, time, sys
import autograd
import autograd.numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import scipy.integrate
solve_ivp = scipy.integrate.solve_ivp

EXPERIMENT_DIR = './experiment-double-embed/'
sys.path.append(EXPERIMENT_DIR)

from data import get_dataset, get_trajectory, dynamics_fn, hamiltonian_fn, arrange_data, get_field
from nn_models import MLP, PSD, DampMatrix
from hnn import HNN, HNN_structure, HNN_structure_embed
from utils import L2_loss, from_pickle

#%%
DPI = 300
FORMAT = 'png'
LINE_SEGMENTS = 10
ARROW_SCALE = 40
ARROW_WIDTH = 6e-3
LINE_WIDTH = 2

def get_args():
    return {'num_angle': 2,
         'learn_rate': 1e-3,
         'nonlinearity': 'tanh',
         'total_steps': 2000,
         'print_every': 200,
         'name': 'pend',
         'gridsize': 10,
         'input_noise': 0.5,
         'seed': 0,
         'save_dir': './{}'.format(EXPERIMENT_DIR),
         'fig_dir': './figures',
         'num_points': 2,
         'gpu': 2,
         'solver': 'rk4'}

class ObjectView(object):
    def __init__(self, d): self.__dict__ = d

args = ObjectView(get_args())

#%%
device = torch.device('cuda:' + str(args.gpu) if torch.cuda.is_available() else 'cpu')
def get_model(args, baseline, structure, naive, damping, num_points):
    M_net = PSD(2*args.num_angle, 400, args.num_angle).to(device)
    g_net = MLP(2*args.num_angle, 300, args.num_angle).to(device)
    if structure == False:
        if naive and baseline:
            raise RuntimeError('argument *baseline* and *naive* cannot both be true')
        elif naive:
            input_dim = 3 * args.num_angle + 1
            output_dim = 3 * args.num_angle
            nn_model = MLP(input_dim, 1000, output_dim, args.nonlinearity).to(device)
            model = HNN_structure_embed(args.num_angle, H_net=nn_model, device=device, baseline=baseline, naive=naive)
        elif baseline:
            input_dim = 3 * args.num_angle + 1
            output_dim = 2 * args.num_angle
            nn_model = MLP(input_dim, 800, output_dim, args.nonlinearity).to(device)
            model = HNN_structure_embed(args.num_angle, H_net=nn_model, M_net=M_net, device=device, baseline=baseline, naive=naive)
        else:
            input_dim = 3 * args.num_angle
            output_dim = 1
            nn_model = MLP(input_dim, 600, output_dim, args.nonlinearity).to(device)
            model = HNN_structure_embed(args.num_angle, H_net=nn_model, M_net=M_net, g_net=g_net, device=device, baseline=baseline, naive=naive)
    elif structure == True and baseline ==False and naive==False:
        V_net = MLP(2*args.num_angle, 200, 1).to(device)
        model = HNN_structure_embed(args.num_angle, M_net=M_net, V_net=V_net, g_net=g_net, device=device, baseline=baseline, structure=True).to(device)
    else:
        raise RuntimeError('argument *structure* is set to true, no *baseline* or *naive*!')

    if naive:
        label = '-naive_ode'
    elif baseline:
        label = '-baseline_ode'
    else:
        label = '-hnn_ode'
    struct = '-struct' if structure else ''
    path = '{}/{}{}{}-{}-p{}.tar'.format(args.save_dir, args.name, label, struct, args.solver, args.num_points)
    model.load_state_dict(torch.load(path, map_location=device))
    path = '{}/{}{}{}-{}-p{}-stats.pkl'.format(args.save_dir, args.name, label, struct, args.solver, args.num_points)
    stats = from_pickle(path)
    return model, stats

# naive_ode_model, naive_ode_stats = get_model(args, baseline=False, structure=False, naive=True, damping=False, num_points=args.num_points)
# base_ode_model, base_ode_stats = get_model(args, baseline=True, structure=False, naive=False, damping=False, num_points=args.num_points)
# hnn_ode_model, hnn_ode_stats = get_model(args, baseline=False, structure=False, naive=False, damping=False, num_points=args.num_points)
hnn_ode_struct_model, hnn_ode_struct_stats = get_model(args, baseline=False, structure=True, naive=False, damping=False, num_points=args.num_points)


#%%
# check training dataset
us = [0.0]
data = get_dataset(seed=args.seed, timesteps=30,
            save_dir=args.save_dir, us=us, samples=200) #us=np.linspace(-2.0, 2.0, 20)
train_x, t_eval = arrange_data(data['x'], data['t'], num_points=args.num_points)
test_x, t_eval = arrange_data(data['test_x'], data['t'], num_points=args.num_points)
#%%

t = 5
cos_q_01 = data['x'][0,t,:,0] ; cos_q_02 = data['x'][0,t,:,1]
sin_q_01 = data['x'][0,t,:,2] ; sin_q_02 = data['x'][0,t,:,3]
q_01_dot = data['x'][0,t,:,4] ; q_02_dot = data['x'][0,t,:,5]

i = 0
# cos_q_01 = data['x'][0,:,i,0] ; cos_q_02 = data['x'][0,:,i,1]
# sin_q_01 = data['x'][0,:,i,2] ; sin_q_02 = data['x'][0,:,i,3]
# q_01_dot = data['x'][0,:,i,4] ; q_02_dot = data['x'][0,:,i,5]
for _ in range(1):
    fig = plt.figure(figsize=[12,3], dpi=DPI)
    plt.subplot(1, 3, 1)
    plt.scatter(np.arctan2(sin_q_02, cos_q_02), q_02_dot)

    plt.subplot(1, 3, 2)
    plt.scatter(np.arctan2(sin_q_01, cos_q_01), q_01_dot)

for _ in range(0):
    fig = plt.figure(figsize=[12,3], dpi=DPI)
    plt.subplot(1, 3, 1)
    plt.plot(q_01_dot)

    plt.subplot(1, 3, 2)
    plt.plot(q_02_dot)



#%% [markdown]
# ## Integrate along vector fields
#%%
# from torchdiffeq import odeint_adjoint as odeint 
from torchdiffeq import odeint
def integrate_model(model, t_span, y0, **kwargs):
    
    def fun(t, np_x):
        x = torch.tensor( np_x, requires_grad=True, dtype=torch.float32).view(1,3*args.num_angle+1).to(device)
        dx = model(0, x).detach().cpu().numpy().reshape(-1)
        return dx

    return solve_ivp(fun=fun, t_span=t_span, y0=y0, **kwargs)

# time info for simualtion
time_step = 100 ; n_eval = 100
t_span = [0,time_step*0.05]
t_linspace_true = np.linspace(t_span[0], time_step, time_step)*0.05
t_linspace_model = np.linspace(t_span[0], t_span[1], n_eval)
# angle info for simuation
q10 = 1.57
q20 = 0.0
# y0 = np.asarray([init_angle, 0])
u0 = 1.0
y0_u = np.asarray([np.cos(q10), np.cos(q20), np.sin(q10), np.sin(q20), 0.0, 0.0, u0])

kwargs = {'t_eval': t_linspace_model, 'rtol': 1e-12, 'method': 'RK45'}

# base_ivp = integrate_model(base_ode_model, t_span, y0_u, **kwargs)
# hnn_ivp = integrate_model(hnn_ode_model, t_span, y0_u, **kwargs)
hnn_struct_ivp = integrate_model(hnn_ode_struct_model, t_span, y0_u, **kwargs)

import gym 
import myenv
env = gym.make('MyAcrobot-v0')
env.reset()
env.state = np.array([q10, q20, 0.0, 0.0], dtype=np.float32)
obs = env._get_ob()
obs_list = []

for _ in range(time_step):
    obs_list.append(obs)
    obs, _, _, _ = env.step([u0])

true_ivp = np.stack(obs_list, 1)
true_ivp = np.concatenate((true_ivp, u0 * np.zeros((1, time_step))), axis=0)

#%%
# comparing true trajectory and the estimated trajectory
# plt.plot(t_linspace_model, base_ivp.y[5,:], 'b-')
# plt.plot(t_linspace_model, hnn_ivp.y[5,:], 'y-')
plt.plot(t_linspace_model, hnn_struct_ivp.y[0,:], 'r-')
plt.plot(t_linspace_true, true_ivp[0,:], 'g')



#%%
# plot learnt function
q = np.linspace(-5.0, 5.0, 40)
q_tensor = torch.tensor(q, dtype=torch.float32).view(40, 1).to(device)
cos_q_sin_q = torch.cat((torch.ones_like(q_tensor), torch.cos(q_tensor), torch.zeros_like(q_tensor), torch.sin(q_tensor)), dim=1)

for _ in range(1):
    fig = plt.figure(figsize=(20, 6.4), dpi=DPI)
    plt.title("Hamiltonian structured ODE NN ({})")

    M_q_inv = hnn_ode_struct_model.M_net(cos_q_sin_q)
    plt.subplot(2, 4, 1)
    plt.plot(q, M_q_inv[:, 0, 0].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[0, 0]", pad=10, fontsize=14)

    plt.subplot(2, 4, 2)
    plt.plot(q, M_q_inv[:, 0, 1].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[0, 1]", pad=10, fontsize=14)

    plt.subplot(2, 4, 3)
    plt.plot(q, M_q_inv[:, 1, 0].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[1, 0]", pad=10, fontsize=14)

    plt.subplot(2, 4, 4)
    plt.plot(q, M_q_inv[:, 1, 1].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[1, 1]", pad=10, fontsize=14)

    V_q = hnn_ode_struct_model.V_net(cos_q_sin_q)
    plt.subplot(2, 4, 5)
    plt.plot(q, V_q.detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$V_q$", rotation=0, fontsize=14)
    plt.title("V_q", pad=10, fontsize=14)

    g_q = hnn_ode_struct_model.g_net(cos_q_sin_q)
    plt.subplot(2, 4, 6)
    plt.plot(q, g_q[:, 0].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$g_q$", rotation=0, fontsize=14)
    plt.title("g_q[0]", pad=10, fontsize=14)

    plt.subplot(2, 4, 7)
    plt.plot(q, g_q[:, 1].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$g_q$", rotation=0, fontsize=14)
    plt.title("g_q[1]", pad=10, fontsize=14)

for _ in range(0):
    fig = plt.figure(figsize=(20, 6.4), dpi=DPI)
    plt.title("Hamiltonian structured ODE NN ({})")

    M_q_inv = hnn_ode_model.M_net(cos_q_sin_q)
    plt.subplot(2, 4, 1)
    plt.plot(q, M_q_inv[:, 0, 0].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[0, 0]", pad=10, fontsize=14)

    plt.subplot(2, 4, 2)
    plt.plot(q, M_q_inv[:, 0, 1].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[0, 1]", pad=10, fontsize=14)

    plt.subplot(2, 4, 3)
    plt.plot(q, M_q_inv[:, 1, 0].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[1, 0]", pad=10, fontsize=14)

    plt.subplot(2, 4, 4)
    plt.plot(q, M_q_inv[:, 1, 1].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$Mq_inv$", rotation=0, fontsize=14)
    plt.title("Mq_inv[1, 1]", pad=10, fontsize=14)

    # V_q = hnn_ode_struct_model.V_net(cos_q_sin_q)
    # plt.subplot(2, 4, 5)
    # plt.plot(q, V_q.detach().cpu().numpy())
    # plt.xlabel("$q$", fontsize=14)
    # plt.ylabel("$V_q$", rotation=0, fontsize=14)
    # plt.title("V_q", pad=10, fontsize=14)

    g_q = hnn_ode_model.g_net(cos_q_sin_q)
    plt.subplot(2, 4, 6)
    plt.plot(q, g_q[:, 0].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$g_q$", rotation=0, fontsize=14)
    plt.title("g_q[0]", pad=10, fontsize=14)

    plt.subplot(2, 4, 7)
    plt.plot(q, g_q[:, 1].detach().cpu().numpy())
    plt.xlabel("$q$", fontsize=14)
    plt.ylabel("$g_q$", rotation=0, fontsize=14)
    plt.title("g_q[1]", pad=10, fontsize=14)


#%%
# vanilla control
# time info for simualtion
time_step = 100 ; n_eval = 100
t_span = [0,time_step*0.05]
t_linspace_true = np.linspace(t_span[0], time_step, time_step)*0.05
t_linspace_model = np.linspace(t_span[0], t_span[1], n_eval)
# angle info for simuation
q10 = 3.14
q20 = 0.0
u0 = 0.0

# y0_u = torch.tensor([np.cos(q10), np.cos(q20), np.sin(q10), np.sin(q20), 0.0, 0.0, u0], requires_grad=True, device=device, dtype=torch.float32).view(1, 7)
# y = y0_u

env.reset()
env.state = np.array([q10, q20, 0.0, 0.0], dtype=np.float32)
obs = env._get_ob()
y = torch.tensor([obs[0], obs[2], obs[1], obs[3], obs[4], obs[5], u0], requires_grad=True, device=device, dtype=torch.float32).view(1, 7)

# goal_state = torch.tensor([0, 1, 1, 0], requires_grad=True, device=device, dtype=torch.float32).view(1, 4)

t_eval = torch.linspace(t_span[0], t_span[1], n_eval).to(device)
rtol = 1e-12

k_p = 1
k_d = 2

y_traj = []
y_traj.append(y)

for i in range(len(t_eval)-1):
    cos_q_sin_q, q_dot, u = torch.split(y, [4, 2, 1], dim=1)
    V_q = hnn_ode_struct_model.V_net(cos_q_sin_q)
    dV = torch.autograd.grad(V_q, cos_q_sin_q)[0]
    dVdcos_q, dVdsin_q= torch.chunk(dV, 2, dim=1)
    cos_q, sin_q = torch.chunk(cos_q_sin_q, 2,dim=1)
    dV_q = - dVdcos_q * sin_q + dVdsin_q * cos_q # (1, 2)
    g_q = hnn_ode_struct_model.g_net(cos_q_sin_q) #(1, 2)
    H, dH = hnn_ode_struct_model.get_H(y)
    dHdcos_q, dHdsin_q, dHdp= torch.split(dH, [2, 2, 2], dim=1)

    norm = torch.sum(g_q * g_q, dim=1)
    # PBC 
    u = torch.sum(g_q * (3*dV_q - 1*dHdp), dim=1).view(1, 1) * 200

    u = u.detach().cpu().numpy()
    obs, _, _, _ = env.step(u)
    y = torch.tensor([obs[0], obs[2], obs[1], obs[3], obs[4], obs[5], u], requires_grad=True, device=device, dtype=torch.float32).view(1, 7)

    # y0_u = torch.cat((cos_q_sin_q, q_dot, u), dim = 1)
    # y_step = odeint(hnn_ode_struct_model, y0_u, t_eval[i:i+2], method='rk4')
    # y = y_step[-1,:,:]
    y_traj.append(y)


#%%
y_traj = torch.stack(y_traj).view(-1, 7).detach().cpu().numpy()


# plot control result
fig = plt.figure(figsize=[10, 10], dpi=DPI)
plt.subplot(7, 1, 1)
plt.plot(t_eval.cpu().numpy(), y_traj[:, 0])

plt.subplot(7, 1, 2)
plt.plot(t_eval.cpu().numpy(), y_traj[:, 1])

plt.subplot(7, 1, 3)
plt.plot(t_eval.cpu().numpy(), y_traj[:, 2])

plt.subplot(7, 1, 4)
plt.plot(t_eval.cpu().numpy(), y_traj[:, 3])

plt.subplot(7, 1, 5)
plt.plot(t_eval.cpu().numpy(), y_traj[:, 4])

plt.subplot(7, 1, 6)
plt.plot(t_eval.cpu().numpy(), y_traj[:, 5])

plt.subplot(7, 1, 7)
plt.plot(t_eval.cpu().numpy(), y_traj[:, 6])


#%%