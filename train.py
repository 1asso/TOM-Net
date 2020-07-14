import torch
import torch.nn as nn
import time
from easydict import EasyDict as edict
from models.model_utils import ModelUtils
from utils.dict_utils import DictUtils
from utils.str_utils import StrUtils
from utils.io_utils import IOUtils
from utils.flow_utils import FlowUtils
from eval.eval_utils import EvalUtils
from criterion.TOMCriterionFlow import TOMCriterionFlow
from criterion.TOMCriterionUnsup import TOMCriterionUnsup

class Trainer:
    def __init__(self, model, config, optim_state):
        print('Initializing Trainer')
        self.config = config
        self.model = model
        self.warping_module = self.setup_warping(config) # reconstruct input based on refractive flow field
        self.optim_state = self.setup_solver(config, optim_state) # check if resume training
        self.setup_criterion(config)
        if not config.refine:
            # in coarse stage, multi scale ground truth matte is needed
            self.multi_scale_data = self.setup_multi_scale_data(config)
        
        print('Get model parameters and gradient parameters')
        self.params = model.parameters()
        print('Total number of parameters in TOM-Net: ' + str(torch.numel(self.params)))
        # variable to store error for the estimated environment matte
        self.flow_e = 0
        self.mask_e = 0
        self.rho_e = 0
    
    def setup_multi_scale_data(config):
        print('[Multi Scale] Setting up multi scale data')
        # generate multi-scale ground truth during training
        ms_data = ModelUtils.create_multi_scale_data(config)
        return ms_data

    def setup_warping(config):
        print('Setting up warping module')
        if config.refine:
            print('[Single Scale] Setting up single scale warping')
            warping_module = ModelUtils.create_single_warping_module()
            self.c_warp = ModelUtils.create_single_warping_module() # for CoarseNet
        else:
            print('[Multi Scale] Setting up multi scale warping')
            warping_module = ModelUtils.create_multi_scale_data(config.ms_num)
        return warping_module

    def setup_criterion(config):
        print('Setting up criterion')
        print('[Flow Loss] Setting up criterion for flow')
        self.flow_crit = TOMCriterionFlow(config)
        if config.refine:
            # for refinement
            # in refinement stage, an addition flow criterion is initialized
            # to calculate the EPE error for CoarseNet
            self.c_flow_crit = TOMCriterionFlow(config)

            print('[Unsup Loss] Setting up criterion for mask, rho and reconstruction image')
            # criterion for mask, attenuation mask and resconstruction loss
            self.unsup_crit = TOMCriterionUnsup(config)

    def setup_solver(config, in_optim_state):
        optim_state = edict()
        if config.solver == 'ADAM':
            print('[Solver] Using Adam solver')
            optim_state = in_optim_state or {
                'learning_rate': config.lr,
                'beta1': config.beta_1,
                'beta2': config.beta_2
            }
        else:
            logging.warning('Unknown optimization method')

        return optim_state

    def get_refine_input(input, predictor):
        c_ls = edict()
        coarse = predictor.forward(input)
        coarse = coarse[-1]

        c_ls.c_loss_flow = self.c_flow_crit.forward(coarse[0], self.flows).c_loss_flow
        c_ls.flow_epe_c = self.get_flow_error(self.c_flow_crit.epe)
        c_ls.mask_error_c = self.get_mask_error(coarse, True)
        c_ls.rho_error_c = self.get_rho_error(coarse, True)

    def train(self, epoch, dataloader, split, *predictor):
        split = split or 'train'
        self.optim_state.learning_rate = self.learning_rate(epoch)
        print('Epoch {}, Learning rate {}'.format(epoch, self.optim_state.learning_rate))
        num_batches = dataloader.batch_size()
        print('====================')
        print(self.optim_state)
        print('====================')
        print('Training epoch # {}, totaling mini batches {}'.format(epoch, num_batches))

        self.model.training()
        crit_output = 0.0
        timer = time.time() # record current time
        times = edict()
        times.data_time = 0
        times.model_time = 0
        times.loss_time = 0
        
        loss = [] # loss every 20 iterations
        losses = [] # loss of entire epochs
        num_batches = dataloader.batch_size()

        def f_eval():
            return crit_output, self.params # TODO - this should be grad params

        for iter, sample in dataloader.run(split, self.config.max_image_num):
            input = self.copy_input_data(sample)
            times.data_time = StrUtils.add_time(times.data_time, timer)
            
            if self.config.refine:
                input, coarse, c_ls = self.get_refine_input(input, predictor)
                DictUtils.dicts_add(loss, c_ls)

            output = self.model.forward(input)

            flows, pred_imgs = self.flow_warping_forward(output) # warp input image with flow
            times.model_time = StrUtils.add_time(times.model_time, timer)
            
            unsup_loss, unsup_grads = self.unsup_crit_forward_backward(output, pred_imgs)
            # loss and grads for object mask, attenuation mask and restruction loss
            DictUtils.dicts_add(loss, unsup_loss)
            warping_grads = self.flow_warping_back(flows, unsup_grads)

            # loss and grads for refractive flow field (supervised loss)
            sup_loss, sup_grads = self.sup_crit_forward_backward(flows)
            DictUtils.dicts_add(loss, sup_loss)
            times.loss_time = StrUtils.add_time(times.loss_time, timer)

            # combine all the gradients for the network
            model_grads = self.get_model_grads(unsup_grads, sup_grads, warping_grads)
            self.model.zero_grad_parameters()
            self.model.backward(input, model_grads)

            # update parameters
            _, tmp_loss = torch.optim.adam(f_eval, self.params, self.optim_state)
            times.model_time = StrUtils.add_time(times.model_time, timer)

            if iter % self.config.train_save == 0:
                if self.config.refine:
                    self.save_refine_results(epoch, iter, output, pred_imgs, split, 1, coarse)
                else:
                    self.save_multi_results(epoch, iter, output, pred_imgs, split)
                print('Save results time: {}'.format(timer.time()))
            # timer.reset()

    def save_refine_results(epoch, iter, output, pred_imgs, split, num, coarse):
        split = split or 'train'
        num = (num > 0 and num < output[0].size()[0]) and num or num < output[0].size()[0]
        c_pred = self.c_warp.forward([self.ref_imgs, coarse[0]])
        for id in range(0, num):
            gt_fcolor = FlowUtils.flow_to_color(self.flows[id])
            results = [self.ref_imgs[id], self.tar_imgs[id], gt_fcolor, self.masks[id]-1, self.rho[id]]
            c_fcolor = FlowUtils.flow_to_color(self.flows[id])
            c_mask = torch.squeeze(EvalUtils.get_mask(coarse[1][[[id]]], True))
            c_rho = coarse[2][id].repeat(3, 1, 1)
            coarse = [False, c_pred[id], c_fcolor, c_mask, c_rho]

            r_fcolor = FlowUtils.flow_to_color(output[0][id])
            r_rho = output[1][id].repeat(3, 1, 1)
            refine = [False, pred_imgs[id], r_fcolor, False, r_rho]

            for val in coarse:
                results.append(val)
            for val in refine:
                results.append(val)

            save_name = self.get_save_name(self.config.log_dir, split, epoch, iter, id)
            IOUtils.save_results_compact(save_name, results, 5)

    def get_save_name(log_dir, split, epoch, iter, id):
        f_path = '{}/{}/Images/'.format(log_dir, split)
        f_names = '{}_{}_{}'.format(epoch, iter, id)
        f_names = '{}_EPE_{}_IoU_{}_Rho_{}'.format(f_names, self.flow_e, self.mask_e, self.rho_e) 
        return os.path.join(f_path, f_names, '.jpg')

    def get_predicts(split, id, output, pred_img, m_scale):
        pred = [] 
        if m_scale:
            gt_color_flow = FlowUtils.flow_to_color(self.multi_flows[m_scale][id])
        else:
            gt_color_flow = FlowUtils.flow_to_color(self.flows[id])
        
        pred.append(gt_color_flow)

        color_flow = FlowUtils.flow_to_color(output[0][id])
        pred.append(color_flow)
        mask = torch.squeeze(EvalUtils.get_mask(output[1][[[id]]], True))
        pred.append(mask)
        rho = output[2][id].repeat(3, 1, 1)
        pred.append(rho)

        if m_scale:
            final_img = EvalUtils.get_final_pred(self.multi_ref_imgs[m_scale][id], pred_img[id], mask, rho)
            first_img = self.multi_tar_imgs[m_scale][id]
        else:
            final_img = EvalUtils.get_final_pred(self.ref_imgs[id], pred_img[id], mask, rho)
            first_img = self.tar_imgs[id]

        pred.insert(0, first_img)
        pred.insert(1, final_img)

    def get_first_row(split, id):
        first = []
        first.append(self.ref_imgs[id])
        first.append(self.tar_imgs[id])
        first.append(False)
        if self.config.in_trimap:
            first.append(self.trimaps[id] / 2.0)
        else:
            first.append(False)
        first.append(self.masks[id] - 1)
        first.append(self.rhos[id])
        return first

    def save_multi_results(epoch, iter, output, multi_pred_img, split, id):
        id = id or 1
        scales = self.config.ms_num
        results = []

        first_row = self.get_first_row(split, id)
        for val in first_row:
            results.append(val)
        
        for i in [scales, 1, -1]:
            pred_img = multi_pred_img[i]
            sub_pred = self.get_predicts(split, id, output[i], pred_img, i)
            for val in sub_pred:
                results.append(val)
        
        save_name = self.get_save_name(self.config.log_dir, split, epoch, iter, id)
        IOUtils.save_results_compact(save_name, results, 6)
        print('Flow magnitude: Mas {}, Min {}, Mean {}'.format(
            torch.max(output[scales][0][id]), torch.min(output[scales][0][id]), 
            torch.mean(torch.abs(output[scales][0][id]))))

    # for image reconstruction loss and image warping
    def flow_warping_forward(output):
        flows = []
        if self.config.refine:
            flows = output[0]
            pred_imgs = self.warping_module.forward([self.ref_imgs, flows])
        else:
            for i in range(self.config.ms_num):
                flows[i] = output[i][0]
            pred_imgs = self.warping_module.forward([self.multi_ref_imgs, flows])
        return flows, pred_imgs

    def flow_warping_back(flows, unsup_grads):
        crit_imgs_grads = []
        if not self.config.refine:
            # refine stage does not use rec_loss
            for i in range(self.config.ms_num):
                crit_imgs_grads[i] = unsup_grads[i][0]
            warping_grads = self.warping_module.backward([self.multi_ref_imgs, flows], crit_imgs_grads)[1]
        return warping_grads

    # for error calculation
    def get_mask_error(output, is_coarse):
        gt_mask = self.masks[0] - 1
        mask = is_coarse and output[1][[[0]]] or output[-1][1][[[0]]]
        pred_mask = EvalUtils.get_mask(mask, False)
        self.mask_e = EvalUtils.cal_IoU_mask(gt_mask, pred_mask)
        return self.mask_e

    def get_rho_error(output, is_coarse):
        gt_mask = self.masks[0] - 1
        gt_rho = self.rhos[0]
        idx = (is_coarse or not self.config.refine) and 2 or 1
        rho = (is_coarse or self.config.refine) and output[idx][0] or output[-1][idx]
        self.rho_e = EvalUtils.cal_err_rho(gt_rho, rho, True, gt_mask)
        return self.rho_e

    def get_flow_error(avg_epe):
        roi_ratio = torch.sum(torch.gt((self.masks - 1), 0.5)) / self.masks.numel()
        if roi_ratio == 0:
            roi_ratio = 1
        self.flow_e = avg_epe / roi_ratio
        return self.flow_e

    def unsup_crit_forward_backward(output, pred_imgs, forward_only):
        crit_input = []
        crit_target = []
        if self.config.refine:
            crit_input.append(output[1])
            crit_target.append(self.rhos)
        else:
            for i in range(self.config.ms_num):
                crit_input[i] = []
                crit_target[i] = []
                w_m = torch.mul(self.multi_flows[i].narrow(2, 3, 1), 
                self.multi_rhos[i].expand_as(self.multi_flows[i]))

                crit_input[i].append(torch.mul(pred_imgs[i], w_m))
                crit_target[i].append(torch.mul(self.multi_tar_imgs[i], w_m))

                crit_input[i].append(output[i][1])
                crit_target[i].append(self.multi_masks[i])

                crit_input[i].append(output[i][2])
                crit_target[i].append(self.multi_rhos[i])

        ls_iter = edict() # loss in this iteration
        ls_iter.rho_error = self.get_rho_error(output)

        if not self.config.refine:
            ls_iter = self.unsup_crit.forward(crit_input, crit_target)
            ls_iter.mask_error = self.get_mask_error(output)
        
        if forward_only:
            return ls_iter
        
        crit_grads = self.unsup_crit.backward(crit_input, crit_target)
        return ls_iter, crit_grads

    def sup_crit_forward_backward(flows, forward_only):
        flow_crit_target = self.config.refine and self.flows or self.multi_flows

        ls_iter = self.flow_crit.forward(flows, flow_crit_target)
        ls_iter.flow_epe = self.get_flow_error(self.flow_crit.epe)

        if forward_only:
            return ls_iter
        
        flow_grads = self.flow_crit.backward(flows, flow_crit_target)
        return ls_iter, flow_grads

    def get_model_grads(unsup_grads, sup_grads, warping_grads):
        model_grads = []
        if self.config.refine:
            flow_grads = sup_grads
            model_grads.append(flow_grads)  # flow
            model_grads.append(unsup_grads[0])  # rho
        else:
            for i in range(self.config.ms_num):
                flow_grads = warping_grads[i]
                flow_grads = torch.add(flow_grads, sup_grads[i])
                model_grads[i] = [flow_grads]  # flow
                unsup_grad = unsup_grads[i]
                model_grads[i].append(unsup_grad[1])  # mask
                model_grads[i].append(unsup_grad[2])  # rho

        return model_grads

    def test(self, epoch, dataloader, split, *predictor):
        timer = time.time()
        num_batches = dataloader.batch_size()

        times = edict()
        times.data_time = 0
        times.model_time = 0
        times.loss_time = 0
        loss = []
        losses = []  # loss in the entire epoch

        print('*** Testing after {} epochs ***'.format(epoch))
        self.model.evaluate()

        for i, sample in enumerate(dataloader.run(split)):
            input = self.copy_input_data(sample)
            times.data_time = StrUtils.add_time(times.data_time, timer)

            if self.config.refine:
                input, coarse, c_ls = self.get_refine_input(input, predictor)
                DictUtils.dicts_add(loss, c_ls)

            output = self.model.forward(input)

            flows, pred_imgs = self.flow_warping_forward(output)
            time.model_time = StrUtils.add_time(times.model_time, timer)

            unsup_loss = self.unsup_crit_forward_backward(output, pred_imgs, True)
            DictUtils.dicts_add(loss, sup_loss)

            sup_loss = self.sup_crit_forward_backward(flows, True)
            DictUtils.dicts_add(loss, sup_loss)
            times.loss_time = StrUtils.add_time(times.loss_time, timer)

            val_disp = (split == 'val') and (iter % self.config.val_display) == 0
            if val_disp:
                losses[iter] = self.display(epoch, iter, num_batches, loss, times, split)
                DictUtils.dict_reset(loss)
                DictUtils.dict_reset(times)
            
            val_save = (split == 'val') and (iter % self.config.val_save) == 0

            if self.config.refine:
                self.save_refine_results(epoch, iter, output, pred_imgs, split, -1 ,coarse)
            elif val_save:
                self.save_multi_results(epoch, iter, output, pred_imgs, split)
            
        average_loss = DictUtils.dict_of_dict_average(losses)
        print(' | Epoch: [{}] Losses summary: {}'.format(epoch, StrUtils.build_loss_string(average_loss)))
        return average_loss

    def display(epoch, iter, num_batches, loss, times, split):
        time_elapsed = StrUtils.time_left(
            self.config.start_time, self.config.n_epochs, num_batches, epoch, iter)
        interval = (split == 'train') and self.config.train_display or self.config.val_display
        loss_average = DictUtils.dict_divide(loss, interval)

        print(' | Epoch ({}): [{}][{}/{}] | {}'.\
            format(split, epoch, iter, num_batches, time_elapsed))
        print(StrUtils.build_loss_string(loss_average))
        print(StrUtils.build_time_string(times))
        return loss_average

    def copy_input_data(sample):
        self.copy_inputs(sample)
        if not self.config.refine:
            self.copy_inputs_multi_scale(sample)

        if self.config.in_trimap:
            network_input = torch.cat(self.tar_imgs, self, trimaps, 1)
        elif self.config.in_bg:
            network_input = torch.cat(self.ref_imgs, self.tar_imgs, 1)
        else:
            network_input = self.tar_imgs
        
        return network_input

    def copy_inputs(sample):
        # copy the input to a CUDA tensor, if using 1 GPU, or to pinned memory,
        # if using DataParallelTable. The target is always copied to a CUDA tensor
        self.ref_imgs = self.ref_imgs or torch.cuda.FloatTensor()
        self.tar_imgs = self.tar_imgs or torch.cuda.FloatTensor()
        self.masks = self.masks or torch.cuda.FloatTensor()
        self.rhos = self.rhos or torch.cuda.FloatTensor()
        self. flows = self.flows or torch.cuda.FloatTensor()
        sz = sample.input.size()
        n, c, h, w = sample.input.size().tolist()

        self.ref_imgs.resize_(n, 3, h, w).copy_(sample.input[[[],[0, 2],[],[]]])
        self.tar_imgs.resize_(n, 3, h, w).copy_(sample.input[[[],[3, 5],[],[]]])
        self.masks.resize(n, h, w).copy_(sample.masks)
        self.rhos.resize(n, h, w).copy_(sample.rhos)
        self.flows.resize(n, 3, h, w).copy_(sample.flows)
        if self. config.in_trimap:
            self.trimaps = self.trimaps or torch.cuda.FloatTensor()
            self.trimaps.resize(n, 1, h, w).copy_(sample.trimaps)

    def copy_inputs_multi_scale(sample):
        multiscale_in = [self.ref_imgs, self.tar_imgs, self.rhos, self.masks, self.flows]

        multiscale_out = self.multi_scale_data.forward(multiscale_in)
        self.multi_ref_imgs = multiscale_out[0]
        self.multi_tar_imgs = multiscale_out[1]
        self.multi_rhos = multiscale_out[2]
        self.multi_masks = multiscale_out[3]
        self.multi_flows = multiscale_out[4]

        for i in range(len(self.multi_flows)):
            # rescale the loss weight for flow in different scale
            ratio = 2 ** (len(self.multi_flows) - i)
            self.multi_flows[i] = torch.mul(self.multi_flows[i].narrow(2, 3, 1), ratio)

    def learning_rate(epoch):
        # training schedule

        ratio = (epoch >= self.config.lr_decay_start and \
            epoch % self.config.lr_decay_step == 0) and 0.5 or 1.0
        return self.optim_state.learning_rate * ratio

    