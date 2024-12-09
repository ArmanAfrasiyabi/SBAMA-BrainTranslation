from model.model_registry import str2model  
from args import params_fn, print_gpu_info 
from args import make_directroy, meg2List 
from torch.optim import Adam    
import torch.nn as nn
import numpy as np
import torch
import time 
import os
from utils.viz_corr import correlation_r2_plot
from visualization.reconstruction.recploter import parcel_rec_scatter, parcel_rec_plot_bayesian
from einops import rearrange

## training
def training(args, model, train_dataloader, valid_dataloader):  
    optimizer = Adam(model.parameters(), args.lr)   
    best_valoss = np.inf
    
    for iteration, ((xm, xf, y_meta, y_batch), [t, sub_f, sub_m]) in enumerate(train_dataloader):  
        
        model.train()   
        loss = model.loss(xm, xf, sub_m, sub_f, iteration)   
        
        optimizer.zero_grad()  
        loss.backward()  
        optimizer.step()  
        
        # validate and save model 
        if iteration == 0  or iteration % args.validation_iteration == 0:  
            valoss_i = validation(args, model, valid_dataloader, iteration) 
            
            if valoss_i < best_valoss:
                best_valoss = valoss_i
                torch.save(
                    {'iteration': iteration, 
                    'model': model.cpu(),  
                    'result_list': model.result_list},
                    args.output_dir + '/xmodel/best_model.pth' 
                ) 
                
            torch.save(
                {'iteration': iteration, 
                'model': model,  
                'result_list': model.result_list},
                args.output_dir + '/xmodel/current_model.pth' 
            ) 
            
            model.print_results(iteration)   
            meg2List(args.output_dir + '/xmodel/results.txt', model.result_list)  
            model.to(args.device) 
        
        # junk save for figure1
        if iteration % (2*args.validation_iteration) == 0:   
            torch.save(
                    {'iteration': iteration, 
                    'model': model,  
                    'result_list': model.result_list},
                    args.output_dir + '/xmodel/'+str(iteration)+'th_model.pth' 
                ) 
 
## validation 
def validation(args, model, valid_dataloader, iteration): 
    first_time = True
    best_valoss = np.inf 
    valoss = 0
    for it_eval, ((xm_i, xf_i, y_meta, y_batch), [miniute_index, sub_f, sub_m]) in enumerate(valid_dataloader):        
        with torch.no_grad():
            model.eval()    
            valoss_i, x_i, x_hat_i, [zm_hrf_i, h_att_i, alphas_i], [x_hemo_hat_std_i, h_att_std_i] = model.loss(xm_i, xf_i, sub_m, sub_f, iteration) 
       
            if first_time:
                first_time = False
                x = x_i[:5, :]
                x_hat = x_hat_i[:5, :]
                if args.mc_probabilistic:
                    x_hat_std = x_hemo_hat_std_i[:5, :]
            else:
                x = torch.cat((x, x_i[5:10, :]), dim=0)
                x_hat = torch.cat((x_hat, x_hat_i[5:10, :]), dim=0)
                if args.mc_probabilistic:
                    x_hat_std = torch.cat((x_hat_std, x_hemo_hat_std_i[5:10, :]), dim=0)
            
            if valoss_i < best_valoss:
                best_valoss = valoss_i
                loss_mse_parcells = valoss_i 
                
            valoss += valoss_i
                 
        if it_eval >= 2:
            break 
        
    args.output_dir = make_directroy(args)   
    correlation_r2_plot(args, iteration, x=x, y=x_hat, title = args.model, folder_extention='')  
     
    # plot the reconstructions
    x = rearrange(x, 'b p t -> p (b t)')
    x_hat = rearrange(x_hat, 'b p t -> p (b t)')
    x_hat_std = rearrange(x_hat_std, 'b p t -> p (b t)') 
    parcel_rec_scatter(args, x_ori=x, x1_hat=x_hat, parcels_name=args.parcels500_name, iteration=iteration) 
    if args.mc_probabilistic:
        parcel_rec_plot_bayesian(args, x_ori=x, x_hat=x_hat, x_hat_std=x_hat_std*1.5, parcels_name=args.parcels500_name, iteration=iteration)
        
    return valoss_i
  
if __name__ == "__main__":  
     
    datasets = ['megfmri', 'eegfmri_translation']  
    
    args = params_fn(server_mode='mccleary', dataset=datasets[0]) 
    args.model = 'SambaEleToHemo' 
    
    args.single_subj = False 
    # args.single_subj = True 
    
    if args.single_subj:
        sub_ele = args.ele_sub_list[0]
        sub_hemo = args.hemo_sub_list[-1]
     
    if args.single_subj:
        args.ele_sub_list = [sub_ele]
        args.hemo_sub_list = [sub_hemo]
        args.save_prefix = 'eleSub' + sub_ele + '_hemoSub' + sub_hemo
    else:
        args.save_prefix = 'allSubs'
         
    if args.dataset=='megfmri':
        from data.dataloader_30_second import  NumpyBatchDataset as NumpyDataset 
    elif args.dataset=='eegfmri_translation':
        from data.dataloader_translation import  NumpyBatchDataset as NumpyDataset 
     
    args.ele_dir = 'MEGfMRI/forrest_gump/30_second_dataset/meg-200parcell-avg/'
    args.hemo_dir = 'MEGfMRI/forrest_gump/30_second_dataset/fmri-500parcell-avg/'
     
    train_dataloader = NumpyDataset(
        meg_dir = args.ele_dir,  
        fmri_dir =  args.hemo_dir,  
        split = 'train',
        n_way = args.n_way,
        fmri_sub_list=args.hemo_sub_list, 
        meg_sub_list=args.ele_sub_list, 
        single_subj=args.single_subj
    )       
      
    test_dataloader = NumpyDataset(
        meg_dir = args.ele_dir,  
        fmri_dir = args.hemo_dir,  
        split = 'test',
        n_way = args.n_way,
        fmri_sub_list=args.hemo_sub_list, 
        meg_sub_list=args.ele_sub_list, 
        single_subj=args.single_subj 
    )     
    
    # build model   
    proto_model = str2model(args.model) 
    model = proto_model(args).to(args.device)  
    args.output_key = time.strftime(args.model+"--"+args.save_prefix+"--%Y%m%d-%H%M%S--" + args.dataset) 
    print_gpu_info(args)  
    
    training(
        args, 
        model, 
        train_dataloader, 
        test_dataloader,  
    )   
  
  
  
   
