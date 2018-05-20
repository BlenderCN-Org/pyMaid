#    This script is part of pymaid (http://www.github.com/schlegelp/pymaid).
#    Copyright (C) 2017 Philipp Schlegel
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along

import collections
import six
import sys
import numpy as np
import json
import pandas as pd
import importlib
import vispy as vp

from pymaid import core, fetch

# Set up logging
import logging
module_logger = logging.getLogger(__name__)
module_logger.setLevel(logging.INFO)
if len(module_logger.handlers) == 0:
    # Generate stream handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    # Create formatter and add it to the handlers
    formatter = logging.Formatter(
        '%(levelname)-5s : %(message)s (%(name)s)')
    sh.setFormatter(formatter)
    module_logger.addHandler(sh)

__all__ = ['neuron2json', 'json2neuron',
           'set_loggers', 'set_pbars', 'eval_skids']

# Import mods from pymaid
mods = {}
for m in ['morpho', 'core', 'plotting', 'graph', 'graph_utils', 'core',
          'connectivity', 'user_stats', 'cluster', 'resample', 'intersect', 'fetch']:
    try:
        mods[m] = importlib.import_module('pymaid.{}'.format(m))
    except:
        pass


def _type_of_script():
    """ Returns context in which pymaid is run. """
    try:
        ipy_str = str(type(get_ipython()))
        if 'zmqshell' in ipy_str:
            return 'jupyter'
        if 'terminal' in ipy_str:
            return 'ipython'
    except:
        return 'terminal'


def is_jupyter():
    """ Test if pymaid is run in a Jupyter notebook."""
    return _type_of_script() == 'jupyter'


def set_loggers(level='INFO'):
    """Helper function to set levels for all associated module loggers."""
    for m in mods:
        mods[m].module_logger.setLevel(level)


def set_pbars(hide=None, leave=None):
    """ Set global progress bar behaviours.

    Parameters
    ----------
    hide :      bool, optional
                Set to True to hide all progress bars.
    leave :     bool, optional
                Set to False to clear progress bars after they have finished.

    Returns
    -------
    Nothing

    """

    if isinstance(hide, bool):
        for m in mods:
            mods[m].pbar_hide = hide

    if isinstance(leave, bool):
        for m in mods:
            mods[m].pbar_leave = leave


def _make_iterable(x, force_type=None):
    """ Helper function. Turns x into a np.ndarray, if it isn't already. For
    dicts, keys will be turned into array.
    """
    if not isinstance(x, collections.Iterable) or isinstance(x, six.string_types):
        x = [x]

    if isinstance(x, dict):
        x = list(x)

    if force_type:
        return np.array(x).astype(force_type)
    else:
        return np.array(x)


def _make_non_iterable(x):
    """ Helper function. Turns x into non-iterable, if it isn't already. Will
    raise error if len(x) > 1.
    """
    if not _is_iterable(x):
        return x
    elif len(x) == 1:
        return x[0]
    else:
        raise ValueError('Iterable must not contain more than one entry.')


def _is_iterable(x):
    """ Helper function. Returns True if x is an iterable but not str or
    dictionary.
    """
    if isinstance(x, collections.Iterable) and not isinstance(x, six.string_types):
        return True
    else:
        return False


def _eval_conditions(x):
    """ Splits list of strings into positive (no ~) and negative (~) conditions
    """

    x = _make_iterable(x, force_type=str)

    return [i for i in x if not i.startswith('~')], [i[1:] for i in x if i.startswith('~')]


def neuron2json(x, **kwargs):
    """ Generate JSON formatted ``str`` respresentation of CatmaidNeuron/List.

    Notes
    -----
    Nodes and connectors are serialised using pandas' ``to_json()``. Most
    other items in the neuron's __dict__ are serialised using
    ``json.dumps()``. Properties not serialised: `._remote_instance`,
    `.graph`, `.igraph`.

    Important
    ---------
    For safety, the :class:`~pymaid.CatmaidInstance` is not serialised as
    this would expose your credentials. Parameters attached to a neuronlist
    are currently not preserved.


    Parameters
    ----------
    x :         {CatmaidNeuron, CatmaidNeuronList}
    **kwargs
                Parameters passed to ``json.dumps()`` and
                ``pandas.DataFrame.to_json()``.

    Returns
    -------
    str

    See Also
    --------
    :func:`~pymaid.json2neuron`
                Read json back into pymaid neurons.

    """

    if not isinstance(x, (core.CatmaidNeuron, core.CatmaidNeuronList)):
        raise TypeError('Unable to convert data of type "{0}"'.format(type(x)))

    if isinstance(x, core.CatmaidNeuron):
        x = core.CatmaidNeuronList([x])

    data = []
    for n in x:
        this_data = {'skeleton_id': n.skeleton_id}

        if 'nodes' in n.__dict__:
            this_data['nodes'] = n.nodes.to_json()

        if 'connectors' in n.__dict__:
            this_data['connectors'] = n.connectors.to_json(**kwargs)

        for k in n.__dict__:
            if k in ['nodes', 'connectors', 'graph', 'igraph', '_remote_instance']:
                continue
            try:
                this_data[k] = n.__dict__[k]
            except:
                module_logger.error('Lost attribute "{0}"'.format(k))

        data.append(this_data)

    return json.dumps(data, **kwargs)


def json2neuron(s, **kwargs):
    """ Load neuron from JSON string.

    Parameters
    ----------
    s :         {str}
                JSON-formatted string.
    **kwargs
                Parameters passed to ``json.loads()`` and
                ``pandas.DataFrame.read_json()``.

    Returns
    -------
    :class:`~pymaid.CatmaidNeuronList`

    See Also
    --------
    :func:`~pymaid.to_json`
                Turn neuron into json.

    """

    if not isinstance(s, str):
        raise TypeError('Need str, got "{0}"'.format(type(s)))

    data = json.loads(s, **kwargs)

    nl = core.CatmaidNeuronList([])

    for n in data:
        # Make sure we have all we need
        REQUIRED = ['skeleton_id']

        missing = [p for p in REQUIRED if p not in n]

        if missing:
            raise ValueError('Missing data: {0}'.format(','.join(missing)))

        cn = core.CatmaidNeuron(int(n['skeleton_id']))

        if 'nodes' in n:
            cn.nodes = pd.read_json(n['nodes'])
            cn.connectors = pd.read_json(n['connectors'])

        for key in n:
            if key in ['skeleton_id', 'nodes', 'connectors']:
                continue
            setattr(cn, key, n[key])

        nl += cn

    return nl


def _eval_remote_instance(remote_instance, raise_error=True):
    """ Evaluates remote instance and checks for globally defined remote
    instances as fall back
    """

    if remote_instance is None:
        if 'remote_instance' in sys.modules:
            return sys.modules['remote_instance']
        elif 'remote_instance' in sys.modules:
            return sys.modules['remote_instance']
        elif 'remote_instance' in globals():
            return globals()['remote_instance']
        else:
            if raise_error:
                raise Exception(
                    'Please either pass a CATMAID instance or define globally as "remote_instance" ')
            else:
                module_logger.warning('No global remote instance found.')
    return remote_instance


def eval_skids(x, remote_instance=None):
    """ Evaluate skeleton IDs. Will turn annotations and neuron names into
    skeleton IDs.

    Parameters
    ----------
    x :             {int, str, CatmaidNeuron, CatmaidNeuronList, DataFrame}
                    Your options are either::
                    1. int or list of ints:
                        - will be assumed to be skeleton IDs
                    2. str or list of str:
                        - if convertible to int, will be interpreted as x
                        - elif start with 'annotation:' will be assumed to be
                          annotations
                        - else, will be assumed to be neuron names
                    3. For CatmaidNeuron/List or pandas.DataFrames/Series:
                        - will look for ``skeleton_id`` attribute
    remote_instance : CatmaidInstance, optional
                      If not passed directly, will try using global.

    Returns
    -------
    list of str
                    List containing skeleton IDs as strings.

    """

    remote_instance = _eval_remote_instance(remote_instance)

    if isinstance(x, (int, np.int64, np.int32, np.int)):
        return str(x)
    elif isinstance(x, (str, np.str)):
        try:
            int(x)
            return str(x)
        except:
            if x.startswith('annotation:'):
                return fetch.get_skids_by_annotation(x[11:], remote_instance=remote_instance)
            elif x.startswith('name:'):
                return fetch.get_skids_by_name(x[5:], remote_instance=remote_instance, allow_partial=False).skeleton_id.tolist()
            else:
                return fetch.get_skids_by_name(x, remote_instance=remote_instance, allow_partial=False).skeleton_id.tolist()
    elif isinstance(x, (list, np.ndarray, set)):
        skids = []
        for e in x:
            temp = eval_skids(e, remote_instance=remote_instance)
            if isinstance(temp, (list, np.ndarray)):
                skids += temp
            else:
                skids.append(temp)
        return sorted(set(skids), key=skids.index)
    elif isinstance(x, core.CatmaidNeuron):
        return [x.skeleton_id]
    elif isinstance(x, core.CatmaidNeuronList):
        return list(x.skeleton_id)
    elif isinstance(x, pd.DataFrame):
        return x.skeleton_id.tolist()
    elif isinstance(x, pd.Series):
        if x.name == 'skeleton_id':
            return x.tolist()
        elif 'skeleton_id' in x:
            return [x.skeleton_id]
        else:
            raise ValueError(
                'Unable to extract skeleton ID from Pandas series {0}'.format(x))
    elif isinstance(x, type(None)):
        return None
    else:
        module_logger.error(
            'Unable to extract x from type %s' % str(type(x)))
        raise TypeError('Unable to extract skids from type %s' % str(type(x)))


def eval_user_ids(x, user_list=None, remote_instance=None):
    """ Checks a list of users and turns them into user IDs. Always
    returns a list! Will attempt converting in the following order:

        (1) user ID
        (2) login name
        (3) last name
        (4) full name
        (5) first name

    Important
    ---------
    Last, first and full names are case-sensitive!

    Parameters
    ----------
    x :         {int, str, list of either}
                Users to check.
    user_list : pd.DataFrame, optional
                User list from :func:`~pymaid.get_user_list`. If you
                already have it, pass it along to save time.

    """

    remote_instance = _eval_remote_instance(remote_instance)

    if x and not isinstance(x, (list, np.ndarray)):
        x = [x]

    try:
        # Test if we have any non IDs (i.e. logins) in users
        user_ids = [int(u) for u in x]
    except:
        # Get list of users if we don't already have it
        if not user_list:
            user_list = fetch.get_user_list(
                remote_instance=remote_instance)

        # Now convert individual entries to user IDs
        user_ids = []
        for u in x:
            try:
                user_ids.append(int(u))
            except:
                for col in ['login', 'last_name', 'full_name', 'first_name']:
                    found = []
                    if u in user_list[col].values:
                        found = user_list[user_list[col] == u].id.tolist()
                        break
                if not found:
                    module_logger.warning(
                        'User "{0}" not found. Skipping...'.format(u))
                elif len(found) > 1:
                    module_logger.warning(
                        'Multiple matching entries for "{0}" found. Skipping...'.format(u))
                else:
                    user_ids.append(int(found[0]))

    return user_ids


def eval_node_ids(x, connectors=True, treenodes=True):
    """ Extract treenode or connector IDs.

    Parameters
    ----------
    x :             {int, str, CatmaidNeuron, CatmaidNeuronList, DataFrame}
                    Your options are either::
                    1. int or list of ints will be assumed to be node IDs
                    2. str or list of str will be checked if convertible to int
                    3. For CatmaidNeuron/List or pandas.DataFrames will try
                       to extract node IDs
    connectors :    bool, optional
                    If True will return connector IDs from neuron objects
    treenodes :     bool, optional
                    If True will return treenode IDs from neuron objects

    Returns
    -------
    list
                    List containing nodes as strings.

    """

    if isinstance(x, (int, np.int64, np.int32, np.int)):
        return [x]
    elif isinstance(x, (str, np.str)):
        try:
            return [int(x)]
        except:
            raise TypeError(
                'Unable to extract node ID from string <%s>' % str(x))
    elif isinstance(x, (list, np.ndarray)):
        # Check non-integer entries
        ids = []
        for e in x:
            temp = eval_node_ids(e, connectors=connectors,
                                 treenodes=treenodes)
            if isinstance(temp, (list, np.ndarray)):
                ids += temp
            else:
                ids.append(temp)
        # Preserving the order after making a set is super costly
        # return sorted(set(ids), key=ids.index)
        return list(set(ids))
    elif isinstance(x, core.CatmaidNeuron):
        to_return = []
        if treenodes:
            to_return += x.nodes.treenode_id.tolist()
        if connectors:
            to_return += x.connectors.connector_id.tolist()
        return to_return
    elif isinstance(x, core.CatmaidNeuronList):
        to_return = []
        for n in x:
            if treenodes:
                to_return += n.nodes.treenode_id.tolist()
            if connectors:
                to_return += n.connectors.connector_id.tolist()
        return to_return
    elif isinstance(x, (pd.DataFrame, pd.Series)):
        to_return = []
        if treenodes and 'treenode_id' in x:
            to_return += x.treenode_id.tolist()
        if connectors and 'connector_id' in x:
            to_return += x.connector_id.tolist()

        if 'connector_id' not in x and 'treenode_id' not in x:
            to_return = x.tolist()

        return to_return
    else:
        raise TypeError(
            'Unable to extract node IDs from type %s' % str(type(x)))

def _parse_objects(x, remote_instance=None):
    """ Helper class to extract objects for plotting.

    Returns
    -------
    skids :     list
    skdata :    pymaid.CatmaidNeuronList
    dotprops :  pd.DataFrame
    volumes :   list
    points :    list of arrays
    """

    if not isinstance(x, list):
        x = [x]

    # Check for skeleton IDs
    skids = []
    for ob in x:
        if isinstance(ob, (str, int)):
            try:
                skids.append(int(ob))
            except:
                pass

    # Collect neuron objects and collate to single Neuronlist
    neuron_obj = [ob for ob in x if isinstance(ob,
                                               (core.CatmaidNeuron,
                                                core.CatmaidNeuronList))]
    skdata = core.CatmaidNeuronList(neuron_obj, make_copy=False)

    # Collect visuals
    visuals = [ob for ob in x if isinstance(ob, vp.scene.visuals.VisualNode)]

    # Collect dotprops
    dotprops = [ob for ob in x if isinstance(ob, core.Dotprops)]

    if len(dotprops) == 1:
        dotprops = dotprops[0]
    elif len(dotprops) == 0:
        dotprops = core.Dotprops()
    elif len(dotprops) > 1:
        dotprops = pd.concat(dotprops)

    # Collect and parse volumes
    volumes = [ob for ob in x if isinstance(ob, (core.Volume, str))]

    # Collect dataframes with X/Y/Z coordinates
    # Note: dotprops and volumes are instances of pd.DataFrames
    dataframes = [ob for ob in x if isinstance(ob, pd.DataFrame) and not isinstance(ob, (core.Dotprops, core.Volume))]
    if [d for d in dataframes if False in [c in d.columns for c in ['x', 'y', 'z']]]:
        module_logger.warning('DataFrames must have x, y and z columns.')
    # Filter to and extract x/y/z coordinates
    dataframes = [d for d in dataframes if False not in [c in d.columns for c in ['x', 'y', 'z']]]
    dataframes = [d[['x', 'y', 'z']].values for d in dataframes]

    # Collect arrays
    arrays = [ob.copy() for ob in x if isinstance(ob, np.ndarray)]
    # Remove arrays with wrong dimensions
    if [ob for ob in arrays if ob.shape[1] != 3]:
        module_logger.warning('Point objects need to be of shape (n,3).')
    arrays = [ob for ob in arrays if ob.shape[1] == 3]

    points = dataframes + arrays

    return skids, skdata, dotprops, volumes, points, visuals
