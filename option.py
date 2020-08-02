import os
import argparse
import numpy as np
import time
import datetime

def get_save_dir_name(args):
    now = datetime.datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H:%M:%S")
    d_name = date + args.prefix + '_' + args.network_type

    params = ['scale_h', 'crop_h', 'flow_w',
                'mask_w', 'rho_w', 'img_w', 'lr']
    for p in params:
        d_name = d_name + '_' + p + '-' + str(vars(args)[p])

    d_name = d_name + ('_trimap' if args.in_trimap else '')
    d_name = d_name + ('_inBg' if args.in_bg else '')
    d_name = d_name + ('_retrain' if args.retrain != 'none' else '')
    d_name = d_name + ('_resume' if args.resume != 'none' else '')
    d_name = d_name + ('_valOnly' if args.val_only else '')

    if args.debug:
        d_name = date + '_' + args.prefix + '_debug'

    log_dir = os.path.join('data/training', d_name, 'logdir')
    save = os.path.join('data/training', d_name, 'checkpointdir')

    return log_dir, save

parser = argparse.ArgumentParser(description='ETOM-Net')

# dataset options
parser.add_argument('--dataset', type=str, default='TOMDataset',
                    help='dataset name')
parser.add_argument('--data_dir', type=str, default='data/datasets/TOM-Net_Synth_Train_178k',
                    help='training dataset path')
parser.add_argument('--train_list', type=str, default='train_simple_98k.txt',
                    help='train list')
parser.add_argument('--val_list', type=str, default='val_imglist.txt',
                    help='val list')
# parser.add_argument('--data_aug', type=bool, default=True,
#                     help='data augmentation')
parser.add_argument('--scale_h', type=int, default=512,
                    help='rescale height')
parser.add_argument('--scale_w', type=int, default=512,
                    help='rescale width')
parser.add_argument('--crop_h', type=int, default=448,
                    help='crop height')
parser.add_argument('--crop_w', type=int, default=448,
                    help='crop width')
parser.add_argument('--noise', type=float, default=0.05,
                    help='noise level')
parser.add_argument('--rot_ang', type=float, default=0.3,
                    help='angle for rotating data')
parser.add_argument('--max_image_num', type=int, default=-1,
                    help='>0 for max number')

# device options
parser.add_argument('--manual_seed', type=int, default=0,
                    help='manually set RNG seed')
parser.add_argument('--cudnn', type=str, default='fastest',
                    help='fastest|default|deterministic')
parser.add_argument('--n_threads', type=int, default=8,
                    help='number of data loading threads')

# training options
parser.add_argument('--start_epoch', type=int, default=1,
                    help='set start epoch for restart')
parser.add_argument('--n_epochs', type=int, default=20,
                    help='number of total epochs to run')
parser.add_argument('--batch_size', type=int, default=4,
                    help='mini-batch size')
parser.add_argument('--lr', type=float, default=1e-4,
                    help='initial learning rate')
parser.add_argument('--lr_decay_start', type=int, default=10,
                    help='number of epochs when lr start to decay')
parser.add_argument('--lr_decay_step', type=int, default=5,
                    help='step for the lr decay')
parser.add_argument('--solver', type=str, default='ADAM',
                    help='solver used(Adam only)')
parser.add_argument('--beta_1', type=float, default=0.9,
                    help='first param of Adam optimizer')
parser.add_argument('--beta_2', type=float, default=0.999,
                    help='second param of Adam optimizer')

# network options
parser.add_argument('--network_type', type=str, default='CoarseNet',
                    help='network type')
# parser.add_argument('--use_BN', type=bool, default=True,
#                     help='use batch norm')
parser.add_argument('--ms_num', type=int, default=4,
                    help='multiscale level')
parser.add_argument('--in_bg', action='store_true',
                    help='take background as input')
parser.add_argument('--in_trimap', action='store_true',
                    help='take trimap as input')
parser.add_argument('--refine', action='store_true',
                    help='train refine net')                    

# checkpoint options
parser.add_argument('--resume', type=str, default='none',
                    help='reload checkpoint and state')
parser.add_argument('--retrain', type=str, default='none',
                    help='reload checkpoint only')
parser.add_argument('--suffix', type=str, default='',
                    help='checkpoint suffix')
parser.add_argument('--save_interval', type=int, default=1,
                    help='epochs to save checkpoint(overwrite)')
parser.add_argument('--save_new', type=int, default=1,
                    help='epochs to save new checkpoint')

# loss options
parser.add_argument('--flow_w', type=float, default=0.01,
                    help='flow weight')
parser.add_argument('--img_w', type=int, default=1,
                    help='image reconstruction weight')
parser.add_argument('--mask_w', type=float, default=0.1,
                    help='mask weight')
parser.add_argument('--rho_w', type=int, default=1,
                    help='attenuation mask weight')

# display options
parser.add_argument('--train_display', type=int, default=20,
                    help='iteration to display train loss')
parser.add_argument('--train_save', type=int, default=300,
                    help='iteration to save train results')
parser.add_argument('--val_interval', type=int, default=1,
                    help='iteration to do validation')
parser.add_argument('--val_display', type=int, default=5,
                    help='iteration to display val loss')
parser.add_argument('--val_save', type=int, default=5,
                    help='iteration to save val results')
parser.add_argument('--val_only', action='store_true',
                    help='run on validation set only')

# log options
parser.add_argument('--prefix', type=str, default='',
                    help='prefix of the log directory')
parser.add_argument('--debug', action='store_true',
                    help='debug mode')

args = parser.parse_args()

args.data_aug = True  # data augmentation
args.use_BN = True  # use batch norm

args.start_time = time.time()
args.log_dir, args.save = get_save_dir_name(args)

if not os.path.isdir(args.log_dir):
    os.makedirs(args.log_dir)

if not os.path.isdir(args.save):
    os.makedirs(args.save)

if args.debug:
    args.max_image_num = 10
    args.train_save = 1
    args.train_display = 1
    args.val_save = 100
