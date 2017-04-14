#
# Oxford Inference Compilation
# https://arxiv.org/abs/1610.09900
#
# Tuan-Anh Le, Atilim Gunes Baydin
# University of Oxford
# May 2016 -- March 2017
#

import infcomp
from infcomp import util
from infcomp.probprog import UniformDiscreteProposal
import torch
import torch.nn as nn
import torch.nn.init as init
import torch.nn.functional as F
from torch.autograd import Variable
import numpy as np
from termcolor import colored
import math
import datetime
import gc

class ProposalUniformDiscrete(nn.Module):
    def __init__(self, input_dim, output_min, output_max, softmax_boost=1.0):
        super(ProposalUniformDiscrete, self).__init__()
        output_dim = output_max - output_min
        self.lin1 = nn.Linear(input_dim, output_dim)
        self.softmax_boost = softmax_boost
        init.xavier_uniform(self.lin1.weight, gain=np.sqrt(2.0))
    def forward(self, x):
        return F.softmax(self.lin1(x).mul_(self.softmax_boost))

class ProposalNormal(nn.Module):
    def __init__(self, input_dim):
        super(ProposalNormal, self).__init__()
        self.lin1 = nn.Linear(input_dim, 2)
        init.xavier_uniform(self.lin1.weight, gain=np.sqrt(2.0))
    def forward(self, x):
        x = self.lin1(x)

class SampleEmbeddingFC(nn.Module):
    def __init__(self, input_dim, output_dim):
        super(SampleEmbeddingFC, self).__init__()
        self.lin1 = nn.Linear(input_dim, output_dim)
        init.xavier_uniform(self.lin1.weight, gain=np.sqrt(2.0))
    def forward(self, x):
        return F.relu(self.lin1(x))

class ObserveEmbeddingFC(nn.Module):
    def __init__(self, input_example_non_batch, output_dim):
        super(ObserveEmbeddingFC, self).__init__()
        self.input_dim = input_example_non_batch.nelement()
        self.lin1 = nn.Linear(self.input_dim, output_dim)
        self.lin2 = nn.Linear(output_dim, output_dim)
        init.xavier_uniform(self.lin1.weight, gain=np.sqrt(2.0))
        init.xavier_uniform(self.lin2.weight, gain=np.sqrt(2.0))
    def forward(self, x):
        x = F.relu(self.lin1(x.view(-1, self.input_dim)))
        x = F.relu(self.lin2(x))
        return x

class ObserveEmbeddingCNN6(nn.Module):
    def __init__(self, input_example_non_batch, output_dim):
        super(ObserveEmbeddingCNN6, self).__init__()
        if input_example_non_batch.dim() == 2:
            self.input_sample = input_example_non_batch.unsqueeze(0).cpu()
        elif input_example_non_batch.dim() == 3:
            self.input_sample = input_example_non_batch.cpu()
        else:
            util.log_error('Expecting a 3d input_example_non_batch (num_channels x height x width) or a 2d input_example_non_batch (height x width). Received: {0}'.format(input_example_non_batch.size()))
        self.input_channels = self.input_sample.size(0)
        self.output_dim = output_dim
        self.conv1 = nn.Conv2d(self.input_channels, 64, 3)
        self.conv2 = nn.Conv2d(64, 64, 3)
        self.conv3 = nn.Conv2d(64, 128, 3)
        self.conv4 = nn.Conv2d(128, 128, 3)
        self.conv5 = nn.Conv2d(128, 128, 3)
        self.conv6 = nn.Conv2d(128, 128, 3)
    def configure(self):
        self.cnn_output_dim = self.forward_cnn(self.input_sample.unsqueeze(0)).view(-1).size(0)
        self.lin1 = nn.Linear(self.cnn_output_dim, self.output_dim)
        self.lin2 = nn.Linear(self.output_dim, self.output_dim)
    def forward_cnn(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = nn.MaxPool2d(2)(x)
        x = F.relu(self.conv3(x))
        x = F.relu(self.conv4(x))
        x = F.relu(self.conv5(x))
        x = nn.MaxPool2d(2)(x)
        x = F.relu(self.conv6(x))
        x = nn.MaxPool2d(2)(x)
        return x
    def forward(self, x):
        if x.dim() == 3:
            x = x.unsqueeze(1) # Add a channel dimension of 1 after the batch dimension. Temporary. This can be removed once we ensure that we always get 2d images as 3d tensors of form (num_channels x height x width) from the protocol.
        x = self.forward_cnn(x)
        x = x.view(-1, self.cnn_output_dim)
        x = F.relu(self.lin1(x))
        x = F.relu(self.lin2(x))
        return x

class Artifact(nn.Module):
    def __init__(self):
        super(Artifact, self).__init__()

        self.sample_layers = {}
        self.proposal_layers = {}
        self.observe_layer = None
        self.lstm = None

        self.model_name = ''
        self.created = datetime.datetime.now()
        self.modified = datetime.datetime.now()
        self.on_cuda = None
        self.code_version = infcomp.__version__
        self.pytorch_version = torch.__version__
        self.standardize = True
        self.one_hot_address = {}
        self.one_hot_instance = {}
        self.one_hot_proposal = {}
        self.one_hot_address_dim = None
        self.one_hot_instance_dim = None
        self.one_hot_proposal_dim = None
        self.valid_size = None
        self.valid_batch = None
        self.lstm_dim = None
        self.lstm_depth = None
        self.lstm_input_dim = None
        self.smp_emb = None
        self.smp_emb_dim = None
        self.obs_emb = None
        self.obs_emb_dim = None
        self.num_parameters = None
        self.train_loss_best = math.inf
        self.train_loss_worst = -math.inf
        self.valid_loss_best = None
        self.valid_loss_worst = None
        self.valid_loss_initial = None
        self.valid_loss_final = None
        self.valid_history_trace = []
        self.valid_history_loss = []
        self.train_history_trace = []
        self.train_history_loss = []
        self.total_training_time = None
        self.total_iterations = None
        self.total_traces = None
        self.updates = 0
        self.optimizer = None
        self.optimizer_state = None

    def get_structure(self):
        ret = str(next(enumerate(self.modules()))[1])
        for p in self.parameters():
            ret = ret + '\n{0} {1}'.format(type(p.data), p.size())
        return ret

    def get_info(self):
        iter_per_sec = self.total_iterations / self.total_training_time.total_seconds()
        traces_per_sec = self.total_traces / self.total_training_time.total_seconds()
        traces_per_iter = self.total_traces / self.total_iterations
        loss_change = self.valid_loss_final - self.valid_loss_initial
        loss_change_per_sec = loss_change / self.total_training_time.total_seconds()
        loss_change_per_iter = loss_change / self.total_iterations
        loss_change_per_trace = loss_change / self.total_traces
        addresses = ' '.join(list(self.one_hot_address.keys()))
        instances = ' '.join(map(str, list(self.one_hot_instance.keys())))
        proposals = ' '.join(list(self.one_hot_proposal.keys()))
        info = '\n'.join(['Model name            : {0}'.format(self.model_name),
                          'Created               : {0}'.format(self.created),
                          'Last modified         : {0}'.format(self.modified),
                          'Code version          : {0}'.format(self.code_version),
                          'Cuda                  : {0}'.format(self.on_cuda),
                          colored('Trainable params      : {:,}'.format(self.num_parameters), 'cyan', attrs=['bold']),
                          colored('Total training time   : {0}'.format(util.days_hours_mins_secs(self.total_training_time)), 'yellow', attrs=['bold']),
                          colored('Updates to file       : {:,}'.format(self.updates), 'yellow'),
                          colored('Iterations            : {:,}'.format(self.total_iterations), 'yellow'),
                          colored('Iterations / s        : {:,.2f}'.format(iter_per_sec), 'yellow'),
                          colored('Total training traces : {:,}'.format(self.total_traces), 'yellow', attrs=['bold']),
                          colored('Traces / s            : {:,.2f}'.format(traces_per_sec), 'yellow'),
                          colored('Traces / iteration    : {:,.2f}'.format(traces_per_iter), 'yellow'),
                          colored('Initial loss          : {:+.6e}'.format(self.valid_loss_initial), 'green'),
                          colored('Final loss            : {:+.6e}'.format(self.valid_loss_final), 'green', attrs=['bold']),
                          colored('Loss change / s       : {:+.6e}'.format(loss_change_per_sec), 'green'),
                          colored('Loss change / iter.   : {:+.6e}'.format(loss_change_per_iter), 'green'),
                          colored('Loss change / trace   : {:+.6e}'.format(loss_change_per_trace), 'green'),
                          colored('Validation set size   : {:,}'.format(self.valid_size), 'green'),
                          colored('Observe embedding     : {0}'.format(self.obs_emb), 'cyan'),
                          colored('Observe emb. dim.     : {:,}'.format(self.obs_emb_dim), 'cyan'),
                          colored('Sample embedding      : {0}'.format(self.smp_emb), 'cyan'),
                          colored('Sample emb. dim.      : {:,}'.format(self.smp_emb_dim), 'cyan'),
                          colored('LSTM dim.             : {:,}'.format(self.lstm_dim), 'cyan'),
                          colored('LSTM depth            : {:,}'.format(self.lstm_depth), 'cyan'),
                          colored('Softmax boost         : {0}'.format(self.softmax_boost), 'cyan'),
                          colored('Addresses             : {0}'.format(addresses), 'yellow'),
                          colored('Instances             : {0}'.format(instances), 'yellow'),
                          colored('Proposals             : {0}'.format(proposals), 'yellow')])
        return info

    def polymorph(self, batch=None):
        if batch is None:
            batch = self.valid_batch

        layers_changed = False
        for sub_batch in batch:
            example_trace = sub_batch[0]
            for sample in example_trace.samples:
                address = sample.address
                instance = sample.instance
                proposal = sample.proposal

                # update the artifact's one-hot dictionary as needed
                self.add_one_hot_address(address)
                self.add_one_hot_instance(instance)
                self.add_one_hot_proposal(proposal)

                # update the artifact's sample and proposal layers as needed
                if not (address, instance) in self.sample_layers:
                    if self.smp_emb == 'fc':
                        sample_layer = SampleEmbeddingFC(sample.value.nelement(), self.smp_emb_dim)
                    else:
                        util.log_error('Unsupported sample embedding: ' + self.smp_emb)
                    if isinstance(proposal, UniformDiscreteProposal):
                        proposal_layer = ProposalUniformDiscrete(self.lstm_dim, proposal.min, proposal.max, self.softmax_boost)
                    else:
                        util.log_error('Unsupported proposal distribution: ' + sample.proposal.name())
                    self.sample_layers[(address, instance)] = sample_layer
                    self.proposal_layers[(address, instance)] = proposal_layer
                    self.add_module('sample_layer({0}, {1})'.format(address, instance), sample_layer)
                    self.add_module('proposal_layer({0}, {1})'.format(address, instance), proposal_layer)
                    util.log_print(colored('Polymorphing, new layers attached : {0}, {1}'.format(address, instance), 'magenta', attrs=['bold']))
                    layers_changed = True

        if layers_changed:
            self.num_parameters = 0
            for p in self.parameters():
                self.num_parameters += p.nelement()
            util.log_print(colored('Polymorphing, new trainable params: {:,}'.format(self.num_parameters), 'magenta', attrs=['bold']))

    def set_sample_embedding(self, smp_emb, smp_emb_dim):
        self.smp_emb = smp_emb
        self.smp_emb_dim = smp_emb_dim

    def set_observe_embedding(self, example_observes, obs_emb, obs_emb_dim):
        self.obs_emb = obs_emb
        self.obs_emb_dim = obs_emb_dim
        if obs_emb == 'fc':
            observe_layer = ObserveEmbeddingFC(Variable(example_observes), obs_emb_dim)
        elif obs_emb == 'cnn6':
            observe_layer = ObserveEmbeddingCNN6(Variable(example_observes), obs_emb_dim)
            observe_layer.configure()
        else:
            util.log_error('Unsupported observation embedding: ' + obs_emb)

        self.observe_layer = observe_layer

    def set_lstm(self, lstm_dim, lstm_depth):
        self.lstm_dim = lstm_dim
        self.lstm_depth = lstm_depth
        self.lstm_input_dim = self.obs_emb_dim + self.smp_emb_dim + self.one_hot_address_dim + self.one_hot_instance_dim + self.one_hot_proposal_dim
        self.lstm = nn.LSTM(self.lstm_input_dim, lstm_dim, lstm_depth)

    def add_one_hot_address(self, address):
        if not address in self.one_hot_address:
            util.log_print(colored('Polymorphing, new address         : ' + address, 'magenta', attrs=['bold']))
            i = len(self.one_hot_address)
            if i >= self.one_hot_address_dim:
                log_error('one_hot_address overflow: {0}'.format(i))
            t = util.Tensor(self.one_hot_address_dim).zero_()
            t.narrow(0, i, 1).fill_(1)
            self.one_hot_address[address] = Variable(t, requires_grad=False)

    def add_one_hot_instance(self, instance):
        if not instance in self.one_hot_instance:
            util.log_print(colored('Polymorphing, new instance        : ' + str(instance), 'magenta', attrs=['bold']))
            i = len(self.one_hot_instance)
            if i >= self.one_hot_instance_dim:
                log_error('one_hot_instance overflow: {0}'.format(i))
            t = util.Tensor(self.one_hot_instance_dim).zero_()
            t.narrow(0, i, 1).fill_(1)
            self.one_hot_instance[instance] = Variable(t, requires_grad=False)

    def add_one_hot_proposal(self, proposal):
        proposal_name = proposal.name()
        if not proposal_name in self.one_hot_proposal:
            util.log_print(colored('Polymorphing, new proposal        : ' + proposal_name, 'magenta', attrs=['bold']))
            i = len(self.one_hot_proposal)
            if i >= self.one_hot_proposal_dim:
                log_error('one_hot_proposal overflow: {0}'.format(i))
            t = util.Tensor(self.one_hot_proposal_dim).zero_()
            t.narrow(0, i, 1).fill_(1)
            self.one_hot_proposal[proposal_name] = Variable(t, requires_grad=False)

    def valid_loss(self):
        loss = 0
        for sub_batch in self.valid_batch:
            loss += self.loss(sub_batch)
        return loss.data[0] / len(self.valid_batch)

    def loss(self, sub_batch):
        gc.collect()
        sub_batch_size = len(sub_batch)
        example_observes = sub_batch[0].observes

        obs = torch.cat([sub_batch[b].observes for b in range(sub_batch_size)])
        if example_observes.dim() == 1:
            obs = obs.view(sub_batch_size, example_observes.size()[0])
        elif example_observes.dim() == 2:
            obs = obs.view(sub_batch_size, example_observes.size()[0], example_observes.size()[1])
        elif example_observes.dim() == 3:
            obs = obs.view(sub_batch_size, example_observes.size()[0], example_observes.size()[1], example_observes.size()[2])
        else:
            util.log_error('Unsupported observation shape: {0}'.format(example_observes.size()))

        observe_embedding = self.observe_layer(Variable(obs, requires_grad=False))

        example_trace = sub_batch[0]

        lstm_input = []
        for time_step in range(example_trace.length):
            sample = example_trace.samples[time_step]
            address = sample.address
            instance = sample.instance
            proposal = sample.proposal

            if time_step == 0:
                sample_embedding = Variable(util.Tensor(sub_batch_size, self.smp_emb_dim).zero_(), requires_grad=False)
            else:
                prev_sample = example_trace.samples[time_step - 1]
                prev_address = prev_sample.address
                prev_instance = prev_sample.instance
                smp = torch.cat([sub_batch[b].samples[time_step - 1].value for b in range(sub_batch_size)]).view(sub_batch_size, prev_sample.value.nelement())
                sample_embedding = self.sample_layers[(prev_address, prev_instance)](Variable(smp, requires_grad=False))

            t = []
            for b in range(sub_batch_size):
                t.append(torch.cat([observe_embedding[b],
                               sample_embedding[b],
                               self.one_hot_address[address],
                               self.one_hot_instance[instance],
                               self.one_hot_proposal[proposal.name()]]))
            t = torch.cat(t).view(sub_batch_size, -1)
            lstm_input.append(t)
        lstm_input = torch.cat(lstm_input).view(example_trace.length, sub_batch_size, -1)

        h0 = Variable(util.Tensor(self.lstm_depth, sub_batch_size, self.lstm_dim).zero_(), requires_grad=False)
        c0 = Variable(util.Tensor(self.lstm_depth, sub_batch_size, self.lstm_dim).zero_(), requires_grad=False)
        lstm_output, _ = self.lstm(lstm_input, (h0, c0))

        logpdf = 0
        for time_step in range(example_trace.length):
            sample = example_trace.samples[time_step]
            address = sample.address
            instance = sample.instance
            proposal = sample.proposal

            proposal_input = lstm_output[time_step]
            proposal_output = self.proposal_layers[(address, instance)](proposal_input)

            if isinstance(proposal, UniformDiscreteProposal):
                log_weights = torch.log(proposal_output + util.epsilon)
                for b in range(sub_batch_size):
                    value = sub_batch[b].samples[time_step].value[0]
                    min = sub_batch[b].samples[time_step].proposal.min
                    logpdf += log_weights[b, int(value) - min] # Should we average this over dimensions? See http://pytorch.org/docs/nn.html#torch.nn.KLDivLoss
            else:
                util.log_error('Unsupported proposal distribution: ' + proposal_type)

        return -logpdf / sub_batch_size
