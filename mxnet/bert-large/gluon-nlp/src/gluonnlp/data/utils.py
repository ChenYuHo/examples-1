# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Utility classes and functions. They help organize and keep statistics of datasets."""
import collections
import errno
import os
import tarfile
import zipfile
import time

import numpy as np
from mxnet.gluon.data import SimpleDataset
from mxnet.gluon.utils import _get_repo_url, check_sha1, download

from .. import _constants as C

__all__ = [
    'Counter', 'count_tokens', 'concat_sequence', 'slice_sequence', 'train_valid_split',
    'line_splitter', 'whitespace_splitter', 'Splitter'
]


class Counter(collections.Counter):  # pylint: disable=abstract-method
    """Counter class for keeping token frequencies."""

    def discard(self, min_freq, unknown_token):
        """Discards tokens with frequency below min_frequency and represents them
        as `unknown_token`.

        Parameters
        ----------
        min_freq: int
            Tokens whose frequency is under min_freq is counted as `unknown_token` in
            the Counter returned.
        unknown_token: str
            The representation for any unknown token.

        Returns
        -------
        The Counter instance.

        Examples
        --------
        >>> a = gluonnlp.data.Counter({'a': 10, 'b': 1, 'c': 1})
        >>> a.discard(3, '<unk>')
        Counter({'a': 10, '<unk>': 2})
        """
        freq = 0
        ret = Counter({})
        for token, count in self.items():
            if count < min_freq:
                freq += count
            else:
                ret[token] = count
        ret[unknown_token] = ret.get(unknown_token, 0) + freq
        return ret


class DefaultLookupDict(dict):
    """Dictionary class with fall-back look-up with default value set in the constructor."""

    def __init__(self, default, d=None):
        if d:
            super(DefaultLookupDict, self).__init__(d)
        else:
            super(DefaultLookupDict, self).__init__()
        self._default = default

    def __getitem__(self, k):
        return self.get(k, self._default)


def count_tokens(tokens, to_lower=False, counter=None):
    r"""Counts tokens in the specified string.

    For token_delim='(td)' and seq_delim='(sd)', a specified string of two sequences of tokens may
    look like::

        (td)token1(td)token2(td)token3(td)(sd)(td)token4(td)token5(td)(sd)


    Parameters
    ----------
    tokens : list of str
        A source list of tokens.
    to_lower : bool, default False
        Whether to convert the source source_str to the lower case.
    counter : Counter or None, default None
        The Counter instance to be updated with the counts of `tokens`. If
        None, return a new Counter instance counting tokens from `tokens`.

    Returns
    -------
    The `counter` Counter instance after being updated with the token
    counts of `source_str`. If `counter` is None, return a new Counter
    instance counting tokens from `source_str`.

    Examples
    --------
    >>> import re
    >>> source_str = ' Life is great ! \n life is good . \n'
    >>> source_str_tokens = filter(None, re.split(' |\n', source_str))
    >>> gluonnlp.data.count_tokens(source_str_tokens)
    Counter({'is': 2, 'Life': 1, 'great': 1, '!': 1, 'life': 1, 'good': 1, '.': 1})

    """
    if to_lower:
        tokens = [t.lower() for t in tokens]

    if counter is None:
        return Counter(tokens)
    else:
        counter.update(tokens)
        return counter


def concat_sequence(sequences):
    """Concatenate sequences of tokens into a single flattened list of tokens.

    Parameters
    ----------
    sequences : list of list of object
        Sequences of tokens, each of which is an iterable of tokens.

    Returns
    -------
    Flattened list of tokens.

    """
    return [token for seq in sequences for token in seq if token]


def slice_sequence(sequence, length, pad_last=False, pad_val=C.PAD_TOKEN, overlap=0):
    """Slice a flat sequence of tokens into sequences tokens, with each
    inner sequence's length equal to the specified `length`, taking into account the requested
    sequence overlap.

    Parameters
    ----------
    sequence : list of object
        A flat list of tokens.
    length : int
        The length of each of the samples.
    pad_last : bool, default False
        Whether to pad the last sequence when its length doesn't align. If the last sequence's
        length doesn't align and ``pad_last`` is False, it will be dropped.
    pad_val : object, default
        The padding value to use when the padding of the last sequence is enabled. In general,
        the type of ``pad_val`` should be the same as the tokens.
    overlap : int, default 0
        The extra number of items in current sample that should overlap with the
        next sample.

    Returns
    -------
    List of list of tokens, with the length of each inner list equal to `length`.

    """
    if length <= overlap:
        raise ValueError('length needs to be larger than overlap')

    if pad_last:
        pad_len = _slice_pad_length(len(sequence), length, overlap)
        sequence = sequence + [pad_val] * pad_len
    num_samples = (len(sequence) - length) // (length - overlap) + 1

    return [sequence[i * (length - overlap): ((i + 1) * length - i * overlap)]
            for i in range(num_samples)]


def _slice_pad_length(num_items, length, overlap=0):
    """Calculate the padding length needed for sliced samples in order not to discard data.

    Parameters
    ----------
    num_items : int
        Number of items in dataset before collating.
    length : int
        The length of each of the samples.
    overlap : int, default 0
        The extra number of items in current sample that should overlap with the
        next sample.

    Returns
    -------
    Length of paddings.

    """
    if length <= overlap:
        raise ValueError('length needs to be larger than overlap')

    step = length - overlap
    span = num_items - length
    residual = span % step
    if residual:
        return step - residual
    else:
        return 0


_vocab_sha1 = {'wikitext-2': 'be36dc5238c2e7d69720881647ab72eb506d0131',
               'gbw': 'ebb1a287ca14d8fa6f167c3a779e5e7ed63ac69f',
               'WMT2014_src': '230ebb817b1d86950d71e2e765f192a4e4f34415',
               'WMT2014_tgt': '230ebb817b1d86950d71e2e765f192a4e4f34415',
               'book_corpus_wiki_en_cased': '2d62af22535ed51f35cc8e2abb607723c89c2636',
               'book_corpus_wiki_en_uncased': 'a66073971aa0b1a262453fe51342e57166a8abcf',
               'openwebtext_book_corpus_wiki_en_uncased':
               'a66073971aa0b1a262453fe51342e57166a8abcf',
               'openwebtext_ccnews_stories_books_cased':
               '2b804f8f90f9f93c07994b703ce508725061cf43',
               'wiki_multilingual_cased': '0247cb442074237c38c62021f36b7a4dbd2e55f7',
               'wiki_cn_cased': 'ddebd8f3867bca5a61023f73326fb125cf12b4f5',
               'wiki_multilingual_uncased': '2b2514cc539047b9179e9d98a4e68c36db05c97a',
               'scibert_scivocab_uncased': '2d2566bfc416790ab2646ab0ada36ba628628d60',
               'scibert_scivocab_cased': '2c714475b521ab8542cb65e46259f6bfeed8041b',
               'scibert_basevocab_uncased': '80ef760a6bdafec68c99b691c94ebbb918c90d02',
               'scibert_basevocab_cased': 'a4ff6fe1f85ba95f3010742b9abc3a818976bb2c',
               'biobert_v1.0_pmc_cased': 'a4ff6fe1f85ba95f3010742b9abc3a818976bb2c',
               'biobert_v1.0_pubmed_cased': 'a4ff6fe1f85ba95f3010742b9abc3a818976bb2c',
               'biobert_v1.0_pubmed_pmc_cased': 'a4ff6fe1f85ba95f3010742b9abc3a818976bb2c',
               'biobert_v1.1_pubmed_cased': 'a4ff6fe1f85ba95f3010742b9abc3a818976bb2c',
               'clinicalbert_uncased': '80ef760a6bdafec68c99b691c94ebbb918c90d02',
               'baidu_ernie_uncased' :'223553643220255e2a0d4c60e946f4ad7c719080',
               'openai_webtext': 'f917dc7887ce996068b0a248c8d89a7ec27b95a1'}


_url_format = '{repo_url}gluon/dataset/vocab/{file_name}.zip'


def train_valid_split(dataset, valid_ratio=0.05):
    """Split the dataset into training and validation sets.

    Parameters
    ----------
    dataset : list
        A list of training samples.
    valid_ratio : float, default 0.05
        Proportion of training samples to use for validation set
        range: [0, 1]

    Returns
    -------
    train : SimpleDataset
    valid : SimpleDataset
    """
    if not 0.0 <= valid_ratio <= 1.0:
        raise ValueError('valid_ratio should be in [0, 1]')

    num_train = len(dataset)
    num_valid = np.ceil(num_train * valid_ratio).astype('int')
    indices = np.arange(num_train)

    np.random.shuffle(indices)
    valid = SimpleDataset([dataset[indices[i]] for i in range(num_valid)])
    train = SimpleDataset([dataset[indices[i + num_valid]] for i in range(num_train - num_valid)])
    return train, valid


def short_hash(name):
    if name not in _vocab_sha1:
        vocabs = list(_vocab_sha1.keys())
        raise ValueError('Vocabulary for {name} is not available. '
                         'Hosted vocabularies include: {vocabs}'.format(name=name,
                                                                        vocabs=vocabs))
    return _vocab_sha1[name][:8]


def _load_pretrained_vocab(name, root, cls=None):
    """Load the accompanying vocabulary object for pre-trained model.

    Parameters
    ----------
    name : str
        Name of the vocabulary, usually the name of the dataset.
    root : str
        Location for keeping the model vocabulary.
    cls : nlp.Vocab or nlp.vocab.BERTVocab, default nlp.Vocab

    Returns
    -------
    Vocab or nlp.vocab.BERTVocab
        Loaded vocabulary object for the pre-trained model.
    """
    file_name = '{name}-{short_hash}'.format(name=name,
                                             short_hash=short_hash(name))
    root = os.path.expanduser(root)
    file_path = os.path.join(root, file_name + '.vocab')
    sha1_hash = _vocab_sha1[name]
    if os.path.exists(file_path):
        if check_sha1(file_path, sha1_hash):
            return _load_vocab_file(file_path, cls)
        else:
            print('Detected mismatch in the content of model vocab file. Downloading again.')
    else:
        print('Vocab file is not found. Downloading.')

    if not os.path.exists(root):
        try:
            os.makedirs(root)
        except OSError as e:
            if e.errno == errno.EEXIST and os.path.isdir(root):
                pass
            else:
                raise e

    prefix = str(time.time())
    zip_file_path = os.path.join(root, prefix + file_name + '.zip')
    repo_url = _get_repo_url()
    if repo_url[-1] != '/':
        repo_url = repo_url + '/'
    download(_url_format.format(repo_url=repo_url, file_name=file_name),
             path=zip_file_path,
             overwrite=True)
    with zipfile.ZipFile(zip_file_path) as zf:
        if not os.path.exists(file_path):
            zf.extractall(root)
    try:
        os.remove(zip_file_path)
    except OSError as e:
        # file has already been removed.
        if e.errno == 2:
            pass
        else:
            raise e

    if check_sha1(file_path, sha1_hash):
        return _load_vocab_file(file_path, cls)
    else:
        raise ValueError('Downloaded file has different hash. Please try again.')


def _load_vocab_file(file_path, cls):
    with open(file_path, 'r') as f:
        if cls is None:
            from ..vocab import Vocab
            cls = Vocab

        return cls.from_json(f.read())


def _extract_archive(file, target_dir):  # pylint: disable=redefined-builtin
    """Extract archive file

    Parameters
    ----------
    file : str
        Absolute path of the archive file.
    target_dir : str
        Target directory of the archive to be uncompressed

    """
    if file.endswith('.gz') or file.endswith('.tar') or file.endswith('.tgz'):
        archive = tarfile.open(file, 'r')
    elif file.endswith('.zip'):
        archive = zipfile.ZipFile(file, 'r')
    else:
        raise Exception('Unrecognized file type: ' + file)
    archive.extractall(path=target_dir)
    archive.close()


def line_splitter(s):
    """Split a string at newlines.

    Parameters
    ----------
    s : str
        The string to be split

    Returns
    --------
    List[str]
        List of strings. Obtained by calling s.splitlines().

    """
    return s.splitlines()


def whitespace_splitter(s):
    """Split a string at whitespace (space, tab, newline, return, formfeed).

    Parameters
    ----------
    s : str
        The string to be split

    Returns
    --------
    List[str]
        List of strings. Obtained by calling s.split().
    """
    return s.split()


class Splitter:
    """Split a string based on a separator.

    Parameters
    ----------
    separator : str
        The separator based on which string is split.
    """

    def __init__(self, separator=None):
        self._separator = separator

    def __call__(self, s):
        """Split a string based on the separator.

        Parameters
        ----------
        s : str
            The string to be split

        Returns
        --------
        List[str]
            List of strings. Obtained by calling s.split(separator).
        """
        return s.split(self._separator)
