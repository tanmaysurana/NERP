"""This section covers `torch` networks for `NERDA`"""
import torch
import torch.nn as nn
from transformers import AutoConfig
from .utils import match_kwargs
from TorchCRF import CRF
import random
import numpy as np

def enforce_reproducibility(seed = 42) -> None:
    """Enforce Reproducibity
    Enforces reproducibility of models to the furthest 
    possible extent. This is done by setting fixed seeds for
    random number generation etcetera. 
    For atomic operations there is currently no simple way to
    enforce determinism, as the order of parallel operations
    is not known.
    Args:
        seed (int, optional): Fixed seed. Defaults to 42.  
    """
    # Sets seed manually for both CPU and CUDA
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    # CUDNN
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    # System based
    random.seed(seed)
    np.random.seed(seed)


class NERDANetwork(nn.Module):
    """A Generic Network for NERDA models.

    The network has an analogous architecture to the models in
    [Hvingelby et al. 2020](http://www.lrec-conf.org/proceedings/lrec2020/pdf/2020.lrec-1.565.pdf).

    Can be replaced with a custom user-defined network with 
    the restriction, that it must take the same arguments.
    """

    def __init__(self, transformer: nn.Module, device: str, n_tags: int, dropout: float = 0.1, fixed_seed=42) -> None:
        """Initialize a NERDA Network

        Args:
            transformer (nn.Module): huggingface `torch` transformer.
            device (str): Computational device.
            n_tags (int): Number of unique entity tags (incl. outside tag)
            dropout (float, optional): Dropout probability. Defaults to 0.1.
        """
        super(NERDANetwork, self).__init__()
        
        enforce_reproducibility()

        # extract transformer name
        transformer_name = transformer.name_or_path
        # extract AutoConfig, from which relevant parameters can be extracted.
        transformer_config = AutoConfig.from_pretrained(transformer_name)

        self.transformer = transformer
        self.dropout = nn.Dropout(dropout)
        self.tags = nn.Linear(transformer_config.hidden_size, n_tags)
        self.device = device

    # NOTE: 'offsets 'are not used in model as-is, but they are expected as output
    # down-stream. So _DON'T_ remove! :)
    def forward(self, 
                input_ids: torch.Tensor, 
                masks: torch.Tensor, 
                token_type_ids: torch.Tensor, 
                target_tags: torch.Tensor, 
                offsets: torch.Tensor) -> torch.Tensor:
        """Model Forward Iteration

        Args:
            input_ids (torch.Tensor): Input IDs.
            masks (torch.Tensor): Attention Masks.
            token_type_ids (torch.Tensor): Token Type IDs.
            target_tags (torch.Tensor): Target tags. Are not used 
                in model as-is, but they are expected downstream,
                so they can not be left out.
            offsets (torch.Tensor): Offsets to keep track of original
                words. Are not used in model as-is, but they are 
                expected as down-stream, so they can not be left out.

        Returns:
            torch.Tensor: predicted values.
        """

        # TODO: can be improved with ** and move everything to device in a
        # single step.
        transformer_inputs = {
            'input_ids': input_ids.to(self.device),
            'masks': masks.to(self.device),
            'token_type_ids': token_type_ids.to(self.device)
            }
        
        # match args with transformer
        transformer_inputs = match_kwargs(self.transformer.forward, **transformer_inputs)
           
        outputs = self.transformer(**transformer_inputs)[0]

        # apply drop-out
        outputs = self.dropout(outputs)

        # outputs for all labels/tags
        outputs = self.tags(outputs)

        return outputs


class TransformerCRF(nn.Module):
    """Transformer + CRF
    """

    def __init__(self, transformer: nn.Module, device: str, n_tags: int, dropout: float = 0.1, fixed_seed=42) -> None:
        """Initialize a NERDA Network

        Args:
            transformer (nn.Module): huggingface `torch` transformer.
            device (str): Computational device.
            n_tags (int): Number of unique entity tags (incl. outside tag)
            dropout (float, optional): Dropout probability. Defaults to 0.1.
        """
        super(TransformerCRF, self).__init__()

        enforce_reproducibility()

        # extract transformer name
        transformer_name = transformer.name_or_path
        # extract AutoConfig, from which relevant parameters can be extracted.
        transformer_config = AutoConfig.from_pretrained(transformer_name)

        self.transformer = transformer
        self.dropout = nn.Dropout(dropout)

        self.classifier = nn.Linear(transformer_config.hidden_size, n_tags)
        self.crf = CRF(n_tags)  # batch_first=True
        self.device = device

    # NOTE: 'offsets 'are not used in model as-is, but they are expected as output
    # down-stream. So _DON'T_ remove! :)
    def forward(self,
                input_ids: torch.Tensor,
                masks: torch.Tensor,
                token_type_ids: torch.Tensor,
                target_tags: torch.Tensor,
                offsets: torch.Tensor) -> torch.Tensor:
        """Model Forward Iteration

        Args:
            input_ids (torch.Tensor): Input IDs.
            masks (torch.Tensor): Attention Masks.
            token_type_ids (torch.Tensor): Token Type IDs.
            target_tags (torch.Tensor): Target tags. Are not used 
                in model as-is, but they are expected downstream,
                so they can not be left out.
            offsets (torch.Tensor): Offsets to keep track of original
                words. Are not used in model as-is, but they are 
                expected as down-stream, so they can not be left out.

        Returns:
            torch.Tensor: predicted values.
        """

        # TODO: can be improved with ** and move everything to device in a
        # single step.
        transformer_inputs = {
            'input_ids': input_ids.to(self.device),
            'masks': masks.to(self.device),
            'token_type_ids': token_type_ids.to(self.device)
        }

        # match args with transformer
        transformer_inputs = match_kwargs(
            self.transformer.forward, **transformer_inputs)

        padded_sequence_output = self.transformer(**transformer_inputs)[0]

        padded_sequence_output = self.dropout(padded_sequence_output)

        logits = self.classifier(padded_sequence_output)

        outputs = (logits,)
        target_tags = target_tags.to(self.device)
        if target_tags is not None:
            loss_mask = target_tags.gt(-1)
            loss = self.crf(logits, target_tags, loss_mask) * (-1)
            outputs = (loss,) + outputs

        # contain: (loss), scores
        return outputs[1]


class TransformerBiLSTMCRF(nn.Module):
    """Transformer + BiLSTM + CRF
    """

    def __init__(self, transformer: nn.Module, device: str, n_tags: int, dropout: float = 0.1, fixed_seed=42) -> None:
        """Initialize a NERDA Network

        Args:
            transformer (nn.Module): huggingface `torch` transformer.
            device (str): Computational device.
            n_tags (int): Number of unique entity tags (incl. outside tag)
            dropout (float, optional): Dropout probability. Defaults to 0.1.
        """
        super(TransformerBiLSTMCRF, self).__init__()

        enforce_reproducibility()

        # extract transformer name
        transformer_name = transformer.name_or_path
        # extract AutoConfig, from which relevant parameters can be extracted.
        transformer_config = AutoConfig.from_pretrained(transformer_name)

        self.transformer = transformer
        self.dropout = nn.Dropout(dropout)

        self.bilstm = nn.LSTM(
            input_size=768,
            hidden_size=transformer_config.hidden_size // 2,
            batch_first=True,
            num_layers=2,
            dropout=dropout,
            bidirectional=True
        )

        self.classifier = nn.Linear(transformer_config.hidden_size, n_tags)
        self.crf = CRF(n_tags)
        self.device = device

    # NOTE: 'offsets 'are not used in model as-is, but they are expected as output
    # down-stream. So _DON'T_ remove! :)
    def forward(self,
                input_ids: torch.Tensor,
                masks: torch.Tensor,
                token_type_ids: torch.Tensor,
                target_tags: torch.Tensor,
                offsets: torch.Tensor) -> torch.Tensor:
        """Model Forward Iteration

        Args:
            input_ids (torch.Tensor): Input IDs.
            masks (torch.Tensor): Attention Masks.
            token_type_ids (torch.Tensor): Token Type IDs.
            target_tags (torch.Tensor): Target tags. Are not used 
                in model as-is, but they are expected downstream,
                so they can not be left out.
            offsets (torch.Tensor): Offsets to keep track of original
                words. Are not used in model as-is, but they are 
                expected as down-stream, so they can not be left out.

        Returns:
            torch.Tensor: predicted values.
        """

        # TODO: can be improved with ** and move everything to device in a
        # single step.
        transformer_inputs = {
            'input_ids': input_ids.to(self.device),
            'masks': masks.to(self.device),
            'token_type_ids': token_type_ids.to(self.device)
        }

        # match args with transformer
        transformer_inputs = match_kwargs(
            self.transformer.forward, **transformer_inputs)

        padded_sequence_output = self.transformer(**transformer_inputs)[0]

        padded_sequence_output = self.dropout(padded_sequence_output)

        lstm_output, _ = self.bilstm(padded_sequence_output)

        logits = self.classifier(lstm_output)

        outputs = (logits,)
        target_tags = target_tags.to(self.device)
        if target_tags is not None:
            loss_mask = target_tags.gt(-1)
            loss = self.crf(logits, target_tags, loss_mask) * (-1)
            outputs = (loss,) + outputs

        # contain: (loss), scores
        return outputs[1]


class TransformerBiLSTM(nn.Module):
    """Transformer + BiLSTM
    """

    def __init__(self, transformer: nn.Module, device: str, n_tags: int, dropout: float = 0.1, fixed_seed=42) -> None:
        """Initialize a NERDA Network

        Args:
            transformer (nn.Module): huggingface `torch` transformer.
            device (str): Computational device.
            n_tags (int): Number of unique entity tags (incl. outside tag)
            dropout (float, optional): Dropout probability. Defaults to 0.1.
        """
        super(TransformerBiLSTM, self).__init__()

        enforce_reproducibility()

        # extract transformer name
        transformer_name = transformer.name_or_path
        # extract AutoConfig, from which relevant parameters can be extracted.
        transformer_config = AutoConfig.from_pretrained(transformer_name)

        self.transformer = transformer
        self.dropout = nn.Dropout(dropout)

        self.bilstm = nn.LSTM(
            input_size=768,
            hidden_size=transformer_config.hidden_size // 2,
            batch_first=True,
            num_layers=2,
            dropout=dropout,
            bidirectional=True
        )

        self.classifier = nn.Linear(transformer_config.hidden_size, n_tags)
        self.device = device

    # NOTE: 'offsets 'are not used in model as-is, but they are expected as output
    # down-stream. So _DON'T_ remove! :)
    def forward(self,
                input_ids: torch.Tensor,
                masks: torch.Tensor,
                token_type_ids: torch.Tensor,
                target_tags: torch.Tensor,
                offsets: torch.Tensor) -> torch.Tensor:
        """Model Forward Iteration

        Args:
            input_ids (torch.Tensor): Input IDs.
            masks (torch.Tensor): Attention Masks.
            token_type_ids (torch.Tensor): Token Type IDs.
            target_tags (torch.Tensor): Target tags. Are not used 
                in model as-is, but they are expected downstream,
                so they can not be left out.
            offsets (torch.Tensor): Offsets to keep track of original
                words. Are not used in model as-is, but they are 
                expected as down-stream, so they can not be left out.

        Returns:
            torch.Tensor: predicted values.
        """

        # TODO: can be improved with ** and move everything to device in a
        # single step.
        transformer_inputs = {
            'input_ids': input_ids.to(self.device),
            'masks': masks.to(self.device),
            'token_type_ids': token_type_ids.to(self.device)
        }

        # match args with transformer
        transformer_inputs = match_kwargs(
            self.transformer.forward, **transformer_inputs)

        padded_sequence_output = self.transformer(**transformer_inputs)[0]

        padded_sequence_output = self.dropout(padded_sequence_output)

        lstm_output, _ = self.bilstm(padded_sequence_output)

        outputs = self.classifier(lstm_output)

        # contain: (loss), scores
        return outputs
