from easydict import EasyDict as edict
import os
import time
import datetime

class Config:
    def parse(args):
        config = edict()

        ## Dataset Configs
        config.dataset = 'TOMDataset'  # Data: Transparent object matting
        config.data_dir = 'data/datasets/TOM-Net_Synth_Train_178k' 
        config.train_list = 'train_simple_98k.txt'  # Data: Train list
        config.val_list = 'val_imglist.txt'  # Data: Val list
        config.data_aug = True  # Data: data augmentation
        config.scale_h = 512  # Data: rescale height
        config.scale_w = 512  # Data: rescale width
        config.crop_h = 448  # Data: crop height
        config.crop_w = 448  # Data: crop width
        config.noise = 0.05  # Data: noisy level
        config.rot_ang = 0.3  # Data: rotate data
        config.max_image_num = -1  # Data: >0 for max numbers

        ## Device Configs

        config.manual_seed = 0  # Device: manually set RNG seed
        config.cudnn = 'fastest'  # Devices: fastest|default|deterministic
        config.n_threads = 8  # Devices: number of data loading threads

        ## Training configs

        config.start_epoch = 1  # Epoch: manual start epoch for restart
        config.n_epochs = 20  # Epoch: number of total epochs to run
        config.batch_size = 4  # Epoch: mini-batch size
        config.lr = 1e-4  # LR: initial learning rate
        config.lr_decay_start = 10  # LR: number of epoch when lr start to decay
        config.lr_decay_step = 5  # LR: step for the lr decay
        config.solver = 'ADAM'  # Solver: Adam only
        config.beta_1 = 0.9  # Solver: first param of Adam optimizer
        config.beta_2 = 0.999  # Solver: second param of Adam optimizer

        ## Network configs

        config.network_type = 'CoarseNet'  # Network: version
        config.use_BN = True  # Network: batch norm
        config.ms_num = 4  # Multiscale: scales level
        config.in_bg = False  # Network: takes background as input
        config.in_trimap = False  # Network: takes trimap as input

        ## Checkpoint configs

        config.resume = 'none'  # Checkpoint: reload checkpoint and state
        config.retrain = 'none'  # Checkpoint: reload checkpoint only
        config.suffix = ''  # Checkpoint: checkpoint suffix
        config.save_interval = 1  # Checkpoint: epochs to save checkpoints (overwrite)
        config.save_new = 1  # Checkpoint: epochs to save new checkpoints

        ## Loss configs

        config.flow_w = 0.01  # Loss: flow weight
        config.img_w = 1  # Loss: image reconstruction weight
        config.mask_w = 0.1  # Loss: mask weight
        config.rho_w = 1  # Loss: attenuation mask weight

        ## Display configs

        config.train_display = 20  # Display: iteration to display train loss
        config.train_save = 300  # Display: iteration to save train results
        config.val_interval = 1  # Display: intervals to do the validation
        config.val_display = 5  # Display: iteration to display val loss
        config.val_save = 5  # Display: iteration to save val results
        config.val_only = False  # Display: run on validation set only

        ## Log configs

        config.prefix = ''  # Log: prefix of the log directory
        config.debug = False  # Log: debug mode


        while args:
            key = args.pop(0)[1:]
            config[key] = args.pop(0)

        config.start_time = time.time()
        config.log_dir, config.save = Config.get_save_dir_name(config)

        if not os.path.isdir(config.log_dir):
            os.makedirs(config.log_dir)
        
        if not os.path.isdir(config.save):
            os.makedirs(config.save)

        if config.debug:
            config.max_image_num = 10
            config.train_save = 1
            config.train_display = 1
            config.val_save = 100


        return config

    def get_save_dir_name(config):
        now = datetime.datetime.now()
        date = now.strftime("%Y-%m-%d")
        time = now.strftime("%H:%M:%S")
        d_name = date + config.prefix + '_' + config.network_type
        
        params = {'scale_h', 'crop_h', 'flow_w', 'mask_w', 'rho_w', 'img_w', 'lr'}
        for p in params:
            d_name = d_name + '_' + p + '-' + str(config[p])
        
        d_name = d_name + ('_trimap' if config.in_trimap else '')
        d_name = d_name + ('_inBg' if config.in_bg else '')
        d_name = d_name + ('_retrain' if config.retrain != 'none' else '')
        d_name = d_name + ('_resume' if config.resume != 'none' else '')
        d_name = d_name + ('_valOnly' if config.val_only else '')

        if config.debug:
            d_name = date + '_' + config.prefix + '_debug'
        
        log_dir = os.path.join('data/training', d_name, 'logdir')
        save = os.path.join('data/training', d_name, 'checkpointdir')

        return log_dir, save