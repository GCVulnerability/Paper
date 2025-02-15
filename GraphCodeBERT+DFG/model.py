import torch
import torch.nn as nn
import torch.nn.functional as F
from utils import *
from Layers import *
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

class GraphCodeBERT(nn.Module):
    def __init__(self, encoder, config, tokenizer, args):
        super(GraphCodeBERT, self).__init__()
        self.encoder = encoder
        self.config = config
        self.tokenizer = tokenizer
        self.args = args
        self.w_embeddings = self.encoder.embeddings.word_embeddings.weight.data.cpu().detach().clone().numpy()
        self.graphEmb = GraphEmbedding(feature_dim_size=768, hidden_size=256, dropout=config.hidden_dropout_prob)
        self.query = 0
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.classifier = PredictionClassification(config, args, input_size= 768)

    def forward(self, inputs_ids=None, attn_mask=None, position_idx=None, labels=None):
        # build DFG
        adj, x_feature = build_dfg(inputs_ids.cpu().detach().numpy(), self.w_embeddings, self.tokenizer)
        adj, adj_mask = preprocess_adj(adj)
        adj_feature = preprocess_features(x_feature)
        adj = torch.from_numpy(adj)
        # print(adj.shape)
        adj_mask = torch.from_numpy(adj_mask)
        adj_feature = torch.from_numpy(adj_feature)
        # print(adj_feature.shape)
        g_emb = self.graphEmb(adj_feature.to(device).double(), adj.to(device).double(), adj_mask.to(device).double())
        nodes_mask = position_idx.eq(0)
        token_mask = position_idx.ge(2)

        inputs_embeddings = self.encoder.embeddings.word_embeddings(
            inputs_ids)
        nodes_to_token_mask = nodes_mask[:, :,
                                         None] & token_mask[:, None, :] & attn_mask
        nodes_to_token_mask = nodes_to_token_mask / \
            (nodes_to_token_mask.sum(-1)+1e-10)[:, :, None]
        avg_embeddings = torch.einsum(
            "abc,acd->abd", nodes_to_token_mask, inputs_embeddings)
        inputs_embeddings = inputs_embeddings * \
            (~nodes_mask)[:, :, None]+avg_embeddings*nodes_mask[:, :, None]
        vec = self.encoder(inputs_embeds=inputs_embeddings,attention_mask=attn_mask, position_ids=position_idx)[0][:, 0, :]
        outputs = self.classifier(torch.cat((vec,g_emb), dim=1))
        logits = outputs
        prob = F.sigmoid(logits)
        if labels is not None:
            labels = labels.float()
            loss = torch.log(prob[:, 0]+1e-10)*labels + \
                torch.log((1-prob)[:, 0]+1e-10)*(1-labels)
            loss = -loss.mean()
            return loss, logits
        else:
            return prob


def distill_loss(logits, knowledge, temperature=10.0):
    loss = F.kl_div(F.log_softmax(logits/temperature), F.softmax(knowledge /
                    temperature), reduction="batchmean") * (temperature**2)
    # Equivalent to cross_entropy for soft labels, from https://github.com/huggingface/transformers/blob/50792dbdcccd64f61483ec535ff23ee2e4f9e18d/examples/distillation/distiller.py#L330

    return loss