import argparse
import glob
import os
import time
from dataset import MultiSessionsGraph
import torch
import pandas as pd
from sklearn.metrics import accuracy_score,f1_score
import torch.nn.functional as F
import sklearn.metrics as metrics
from models import Model,Net,Net2,Net3
from torch.utils.data import random_split
from torch_geometric.data import DataLoader
from torch_geometric.datasets import TUDataset
import numpy as np
from sklearn.model_selection import KFold
from torch.autograd import Variable
from GIN import GIN

Name=[[] for i in range(1201)]

import matplotlib.pyplot as plt
from sklearn.metrics import (
    precision_recall_curve,
    auc,
    precision_recall_fscore_support,
    roc_auc_score,
    average_precision_score,
)

parser = argparse.ArgumentParser()

def print_to_file(filename, string_info, mode="a"):
	with open(filename, mode) as f:
		f.write(str(string_info) + "\n")

parser.add_argument('--seed', type=int, default=777, help='random seed')
parser.add_argument('--batch_size', type=int, default=100, help='batch size')
parser.add_argument('--lr', type=float, default=0.002, help='learning rate')
parser.add_argument('--weight_decay', type=float, default=0.001, help='weight decay')
parser.add_argument('--nhid', type=int, default=256, help='hidden size')
parser.add_argument('--average', type=bool, default=False)
parser.add_argument('--num_layers', type=int, default=3)
parser.add_argument('--sample_neighbor', type=bool, default=True, help='whether sample neighbors')
parser.add_argument('--sparse_attention', type=bool, default=True, help='whether use sparse attention')
parser.add_argument('--structure_learning', type=bool, default=True, help='whether perform structure learning')
parser.add_argument('--pooling_ratio', type=float, default=0.5, help='pooling ratio')
parser.add_argument('--dropout_ratio', type=float, default=0.6, help='dropout ratio')
parser.add_argument('--lamb', type=float, default=1.0, help='trade-off parameter')
parser.add_argument('--device', type=str, default='cuda:0', help='specify cuda devices')
parser.add_argument('--epochs', type=int, default=50, help='maximum number of epochs')
parser.add_argument('--patience', type=int, default=100, help='patience for early stopping')
parser.add_argument('--conv_name', type=str, default='ARMAConv', help='conv')
parser.add_argument('--pool_name', type=str, default='EdgePooling', help='pooling')
parser.add_argument('--PATH', type=str, default='./data/data_origin', help='dataset')
parser.add_argument('--ignore_edge', type=bool, default=False, help='whether ignore edge')
parser.add_argument('--ignore_node_attr', type=bool, default=False, help='whether ignore node attribute')

args = parser.parse_args()
torch.manual_seed(args.seed)
if torch.cuda.is_available():
    torch.cuda.manual_seed(args.seed)
end_path=os.path.basename(args.PATH)
if end_path == 'data':
    processed_path='./core'
elif end_path == 'data_origin':
    processed_path='./full'
else:
    processed_path='./data'
dataset = MultiSessionsGraph(processed_path,PATH=args.PATH)

dataset = dataset.shuffle()
dataset = dataset.shuffle()
dataset = dataset.shuffle()

args.num_classes = 2

if args.ignore_node_attr:
    args.num_features=1
elif args.ignore_edge:
    args.num_features=2
else:
    args.num_features = dataset.num_features

print(args)

# num_training = int(len(dataset) * 0.9)
# num_val = int(len(dataset) * 0.1)
# train_loader = DataLoader(training_set, batch_size=args.batch_size, shuffle=True)
# val_loader = DataLoader(validation_set, batch_size=args.batch_size, shuffle=False)

FOLDS = 10
k_fold = KFold(n_splits=FOLDS, shuffle=True, random_state=1245)

def train(training_set,validation_set):

    train_loader = DataLoader(training_set, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(validation_set, batch_size=args.batch_size, shuffle=False)
    model = Model(args).to(args.device)
    # model = GIN().to(args.device)
    # model = Net(input_dim=dataset.num_features, hidden_dim=64, num_classes=2).to(args.device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    min_loss = 1e10
    patience_cnt = 0
    best_epoch = 0

    t = time.time()
    model.train()
    for epoch in range(args.epochs):
        loss_train = 0.0
        correct = 0
        for i, data in enumerate(train_loader):
            if args.ignore_edge:
                tmp = [[] for i in range(data.edge_index.shape[0])]
                data.edge_index=torch.tensor(tmp,dtype=torch.long)
                data.edge_attr=torch.tensor([[]],dtype=torch.long).t()
                data.x=data.x[:,1:3]
                if data.x.shape[1] !=2:
                    raise 'x.shape != 2'
            if args.ignore_node_attr:
                data.x=torch.ones(data.x.shape[0],1,dtype=data.x.dtype)
            optimizer.zero_grad()
            y=[]
            graph_hindex=[]
            temp = torch.reshape(data.y,(-1,1))
            for i in range(temp.shape[0]):
                y.append(int(temp[i][0]))
                graph_hindex.append(np.array(temp[i][:0]))

            y=Variable(torch.LongTensor(y)).to(args.device)
            # graph_hindex=Variable(torch.FloatTensor(np.array(graph_hindex))).to(args.device)

            # lens=[0]*(data.batch[data.batch.shape[0]-1]+1)
            paper_count=[0]*(data.batch[data.batch.shape[0]-1]+1)
            for j in range(data.batch.shape[0]):
                paper_count[data.batch[j]]+=1
            # lens=Variable(torch.FloatTensor(lens)).to(args.device)
            paper_count=Variable(torch.FloatTensor(paper_count)).to(args.device)
            data = data.to(args.device)
            
            out = model(data,paper_count,args.PATH)
            # out = model(data,paper_count)
            # out = model(data)
            # out,loss_pool = model(data.x,data.edge_index,data.batch)
            weights = np.array([1,1])
            weights = torch.FloatTensor(weights).to(args.device)
            loss = F.nll_loss(out, y, weight=weights)
            loss.backward()
            optimizer.step()
            loss_train += loss.item()
            pred = out.max(dim=1)[1]
            correct += pred.eq(y.to(args.device)).sum().item()
        acc_train = correct / len(train_loader.dataset)
        acc_val, loss_val, preds, lables, y_proba = compute_test(val_loader,model,epoch)
        # acc_val, loss_val, preds, lables, y_proba = compute_test(val_loader,model)

        # print('Epoch: {:04d}'.format(epoch + 1), 'loss_train: {:.6f}'.format(loss_train),
        #       'acc_train: {:.6f}'.format(acc_train), 'loss_val: {:.6f}'.format(loss_val),
        #       'acc_val: {:.6f}'.format(acc_val), 'time: {:.6f}s'.format(time.time() - t))
        

    print('Optimization Finished! Total time elapsed: {:.6f}'.format(time.time() - t))

    return preds, lables, y_proba
    # return preds, lables, y_proba


def compute_test(loader,model,epoch):
    model.eval()
    correct = 0.0
    loss_test = 0.0
    for data in loader:
        if args.ignore_edge:
            tmp = [[] for i in range(data.edge_index.shape[0])]
            data.edge_index = torch.tensor(tmp, dtype=torch.long)
            data.edge_attr = torch.tensor([[]], dtype=torch.long).t()
            data.x=data.x[:,1:3]
            if data.x.shape[1] !=2:
                raise 'x.shape != 2'
        if args.ignore_node_attr:
            data.x = torch.ones(data.x.shape[0], 1, dtype=data.x.dtype)
        y=[]
        graph_hindex=[]
        temp = torch.reshape(data.y,(-1,1))
        for i in range(temp.shape[0]):
            y.append(int(temp[i][0]))
            graph_hindex.append(np.array(temp[i][:0]))

        y=Variable(torch.LongTensor(y)).to(args.device)
        # graph_hindex=Variable(torch.FloatTensor(np.array(graph_hindex))).to(args.device)

        # lens=[0]*(data.batch[data.batch.shape[0]-1]+1)
        paper_count=[0]*(data.batch[data.batch.shape[0]-1]+1)
        for j in range(data.batch.shape[0]):
            # if(data.x[j][3]==1):
            #     lens[data.batch[j]]+=1
            paper_count[data.batch[j]]+=1
        # lens=Variable(torch.FloatTensor(lens)).to(args.device)
        paper_count=Variable(torch.FloatTensor(paper_count)).to(args.device)

        data = data.to(args.device)
        
        out = model(data,paper_count,args.PATH)
        # out = model(data,paper_count)
        # out = model(data)
        # out,loss_pool = model(data.x,data.edge_index,data.batch)

        pred = out.max(dim=1)[1]
        correct += pred.eq(y).sum().item()
        loss_test += (F.nll_loss(out, y)).item()
        preds=pred.cpu().long().numpy()
        labels=torch.squeeze(y).cpu()
        labels=labels.long().numpy()
        y_proba=F.softmax(out).cpu().detach().numpy()
        if epoch==(args.epochs-1):
            name=data.name.cpu().detach().numpy()
            for idx in range(name.shape[0]):
                # print(name[idx],preds[idx])
                Name[name[idx]].append(preds[idx])

    # return correct / len(loader.dataset), loss_test, preds, lables, y_proba, cos.cpu().detach().numpy()
    return correct / len(loader.dataset), loss_test, preds, labels, y_proba

def f1(precision,recall):
    if(precision==0 or recall==0):
        return 0
    return (recall*precision*2)/(recall+precision)

def f2(precision,recall):
    if(precision==0 or recall==0):
        return 0
    return (recall*precision*5)/(recall+4*precision)


if __name__ == '__main__':
    # Model training
    Precision = []
    Recall = []
    F_measure = []
    ROC = []
    PRC = []
    F1_best = []
    F1_05 = []
    F2 = []
    ACC = []
    F2_best = []
    cos_ave = []
    # torch.backends.cudnn.enabled=False
    round=10
    for r in range(round):
        y_pred = []
        y_real = []
        y_proba_minority = []
        dataset = dataset.shuffle()
        for i, (train_index, test_index) in enumerate(k_fold.split(dataset)):
            print(len(train_index),len(test_index))
            training_set=dataset[train_index]
            validation_set=dataset[test_index]
            # preds, lables, y_proba, cos = train(training_set,validation_set)
            preds, lables, y_proba = train(training_set,validation_set)

            y_pred.append(preds)
            y_real.append(lables)

            # cos_ave.append(cos)

            y_proba_minority.append(y_proba)
        y_pred = np.concatenate(y_pred)
        y_real = np.concatenate(y_real)
        # print(y_pred.shape)
        y_proba_minority = np.concatenate(y_proba_minority)

        f1_05 = f1_score(y_real,y_proba_minority[:,1]>=0.5)
        roc = roc_auc_score(y_real, y_proba_minority[:,1])
        prc = average_precision_score(
            y_real,
            y_proba_minority[:,1],
            pos_label=1,
        )
        precision, recall, thresholds = precision_recall_curve(
            y_real,
            y_proba_minority[:,1],
            pos_label=1,
        )
        recall_=0
        precision_=0
        Thresholds=0
        f1_best=0
        for pre_i in range(len(precision)):
            f=f1(precision[pre_i],recall[pre_i])
            if(f>f1_best):
                f1_best=f
                Thresholds=thresholds[pre_i]
                recall_=recall[pre_i]
                precision_=precision[pre_i]
        F1_05.append(f1_05)
        F1_best.append(f1_best)
        y_pred = (y_proba_minority[:,1] >= Thresholds).astype(int)
        acc=accuracy_score(y_real,y_pred)

        for label_index, label in enumerate([1]):
            print(
                "Overall Precision, Recall, ROC, PRC, ACC for Class",
                label,
                ": ",
                precision_,
                ", ",
                recall_,
                ", ",
                f1_05,
                ", ",
                f1_best,
                ", ",
                roc,
                ", ",
                prc,
                ", ",
                acc
            )
        
        Precision.append(precision_)
        Recall.append(recall_)
        ROC.append(roc)
        PRC.append(prc)
        ACC.append(acc)

    Stability=[]
    for id_name in Name:
        if(id_name!=[]):
            id_name = np.array(id_name)
            counts = np.bincount(id_name)
            stability = np.max(counts)/id_name.shape[0]
            Stability.append(stability)
    Stability=np.array(Stability)
    print("Stability:",np.mean(Stability))
    print("ignore_edge:", args.ignore_edge)
    print("PATH:", args.PATH)
    comment=''
    if args.ignore_node_attr:
        comment+="ignore_node_attr "
    if args.ignore_edge:
        recordfile="Nedge_"
        comment+="ignore_edge "
    else:
        recordfile="edge_"
    
    recordfile+="hI_"
    if end_path == 'data':
        recordfile+='core'
    elif end_path == 'data_origin':
        recordfile+='full'
    else:
        recordfile+=end_path
    recordfile+='.txt'
    # print_to_file("record.txt",comment+args.PATH+' '+args.conv_name+f"{round}*10-fold-cv Precision:%.3f Recall:%.3f F1_05:%.3f F1_05(std):%.3f F1_best:%.3f F1_best(std): %.3f ROC:%.3f ROC(std): %.3f PRC:%.3f ACC:%.3f for Class Fellow" % (np.mean(Precision),np.mean(Recall),np.mean(F1_05),np.std(F1_05),np.mean(F1_best),np.std(F1_best),np.mean(ROC),np.std(ROC),np.mean(PRC),np.mean(ACC)))
    # print(f"{round}*10-fold-cv Precision, Recall, F1_05, F1_05(std), F1_best, F1_best(std), ROC, ROC(std), PRC, ACC for Class:",np.mean(Precision),np.mean(Recall),np.mean(F1_05),np.std(F1_05),np.mean(F1_best),np.std(F1_best),np.mean(ROC),np.std(ROC),np.mean(PRC),np.mean(ACC))
    print_to_file(recordfile,f"--PATH {args.PATH} --lr {args.lr} --nhid {args.nhid} --dropout_ratio {args.dropout_ratio} --conv_name {args.conv_name} --num_layers {args.num_layers} --average({args.average}) {comment} "+f"{round}*10-fold-cv Precision:%.3f Recall:%.3f F1_best:%.3f F1_best(std): %.3f ROC:%.3f ROC(std): %.3f PRC:%.3f ACC:%.3f for Class Fellow" % (np.mean(Precision),np.mean(Recall),np.mean(F1_best),np.std(F1_best),np.mean(ROC),np.std(ROC),np.mean(PRC),np.mean(ACC)))
    print(f"{round}*10-fold-cv Precision, Recall, F1_best, F1_best(std), ROC, ROC(std), PRC, ACC for Class:",np.mean(Precision),np.mean(Recall),np.mean(F1_best),np.std(F1_best),np.mean(ROC),np.std(ROC),np.mean(PRC),np.mean(ACC))

