# code borrowed from Sam Greydanus
# https://github.com/greydanus/hamiltonian-nn

import torch, argparse
import numpy as np

import os, sys
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PARENT_DIR)

from nn_models import MLP, PSD, ConstraintNet
from hnn import HNN_structure_forcing
from data import get_dataset, arrange_data
from utils import L2_loss, to_pickle

import time

def get_args():
    parser = argparse.ArgumentParser(description=None)
    parser.add_argument('--input_dim', default=3, type=int, help='dimensionality of input tensor')
    parser.add_argument('--hidden_dim', default=600, type=int, help='hidden dimension of mlp')
    parser.add_argument('--learn_rate', default=1e-3, type=float, help='learning rate')
    parser.add_argument('--nonlinearity', default='tanh', type=str, help='neural net nonlinearity')
    parser.add_argument('--total_steps', default=2000, type=int, help='number of gradient steps')
    parser.add_argument('--print_every', default=200, type=int, help='number of gradient steps between prints')
    parser.add_argument('--name', default='pend', type=str, help='only one option right now')
    parser.add_argument('--baseline', dest='baseline', action='store_true', help='run baseline or experiment?')
    parser.add_argument('--verbose', dest='verbose', action='store_true', help='verbose?')
    parser.add_argument('--seed', default=0, type=int, help='random seed')
    parser.add_argument('--save_dir', default=THIS_DIR, type=str, help='where to save the trained model')
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--num_points', type=int, default=2, help='number of evaluation points by the ODE solver, including the initial point')
    parser.add_argument('--structure', dest='structure', action='store_true', help='using a structured Hamiltonian')
    parser.add_argument('--gym', dest='gym', action='store_true', help='use OpenAI gym to generate data')
    parser.add_argument('--rad', dest='rad', action='store_true', help='generate random data around a radius')
    parser.set_defaults(feature=True)
    return parser.parse_args()

def train(args):
    # import ODENet
    from torchdiffeq import odeint

    device = torch.device('cuda:' + str(args.gpu) if torch.cuda.is_available() else 'cpu')

    # reproducibility: set random seed
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    # init model and optimizer
    if args.verbose:
        print("Training baseline ODE model with num of points = {}:".format(args.num_points) if args.baseline 
            else "Training HNN ODE model with num of points = {}:".format(args.num_points))
        if args.structure:
            print("using the structured Hamiltonian")
    
    if args.structure == False:
        output_dim = args.input_dim-1 if args.baseline else 1
        nn_model = MLP(args.input_dim-1, args.hidden_dim, output_dim, args.nonlinearity).to(device)
        g_net = MLP(int(args.input_dim/3), args.hidden_dim, int(args.input_dim/3)).to(device)
        model = HNN_structure_forcing(args.input_dim, H_net=nn_model, g_net=g_net, device=device, baseline=args.baseline)
    elif args.structure == True and args.baseline ==False:
    
        M_net = MLP(1, args.hidden_dim, 1).to(device)
        V_net = MLP(int(args.input_dim/3), 50, 1).to(device)
        g_net = MLP(int(args.input_dim/3), args.hidden_dim, int(args.input_dim/3)).to(device)
        model = HNN_structure_forcing(args.input_dim, M_net=M_net, V_net=V_net, g_net=g_net, device=device, baseline=args.baseline, structure=True).to(device)
    else:
        raise RuntimeError('argument *baseline* and *structure* cannot both be true')


    optim = torch.optim.Adam(model.parameters(), args.learn_rate, weight_decay=1e-4)

    # arrange data
    data = get_dataset(seed=args.seed, gym=args.gym, save_dir=args.save_dir, rad=args.rad, us=[-2.0, -1.0, 0.0, 1.0, 2.0]) #us=np.linspace(-2.0, 2.0, 20)
    train_x, t_eval = arrange_data(data['x'], data['t'], num_points=args.num_points)
    test_x, t_eval = arrange_data(data['test_x'], data['t'], num_points=args.num_points)

    train_x = torch.tensor(train_x, requires_grad=True, dtype=torch.float32).to(device) # (45, 25, 2)
    test_x = torch.tensor(test_x, requires_grad=True, dtype=torch.float32).to(device)
    t_eval = torch.tensor(t_eval, requires_grad=True, dtype=torch.float32).to(device)

    # training loop
    stats = {'train_loss': [], 'test_loss': [], 'forward_time': [], 'backward_time': [],'nfe': []}
    for step in range(args.total_steps+1):
        train_loss = 0
        test_loss = 0
        for i in range(train_x.shape[0]):
            
            t = time.time()
            train_x_hat = odeint(model.time_derivative, train_x[i, 0, :, :], t_eval, method='rk4')            
            forward_time = time.time() - t
            train_loss_mini = L2_loss(train_x[i,:,:,:], train_x_hat)
            train_loss = train_loss + train_loss_mini

            t = time.time()
            train_loss_mini.backward() ; optim.step() ; optim.zero_grad()
            backward_time = time.time() - t

            # run test data
            test_x_hat = odeint(model.time_derivative, test_x[i, 0, :, :], t_eval, method='rk4')
            test_loss_mini = L2_loss(test_x[i,:,:,:], test_x_hat)
            test_loss = test_loss + test_loss_mini

        # logging
        stats['train_loss'].append(train_loss.item())
        stats['test_loss'].append(test_loss.item())
        stats['forward_time'].append(forward_time)
        stats['backward_time'].append(backward_time)
        stats['nfe'].append(model.nfe)
        if args.verbose and step % args.print_every == 0:
            print("step {}, train_loss {:.4e}, test_loss {:.4e}".format(step, train_loss.item(), test_loss.item()))

    return model, stats





if __name__ == "__main__":
    args = get_args()
    model, stats = train(args)

    # save 
    os.makedirs(args.save_dir) if not os.path.exists(args.save_dir) else None
    label = '-baseline_ode' if args.baseline else '-hnn_ode'
    struct = '-struct' if args.structure else ''
    rad = '-rad' if args.rad else ''
    path = '{}/{}{}{}-p{}{}.tar'.format(args.save_dir, args.name, label, struct, args.num_points, rad)
    torch.save(model.state_dict(), path)
    path = '{}/{}{}{}-p{}-stats{}.pkl'.format(args.save_dir, args.name, label, struct, args.num_points, rad)
    to_pickle(stats, path)