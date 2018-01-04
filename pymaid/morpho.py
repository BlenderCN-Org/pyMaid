#    This script is part of pymaid (http://www.github.com/schlegelp/pymaid).
#    Copyright (C) 2017 Philipp Schlegel
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along


""" This module contains functions to analyse and manipulate neuron morphology.
"""

import math
import time
import logging
import pandas as pd
import numpy as np
import scipy
from tqdm import tqdm, trange
import itertools

from pymaid import fetch, core, graph_utils

# Set up logging
module_logger = logging.getLogger(__name__)
module_logger.setLevel(logging.INFO)

if len( module_logger.handlers ) == 0:
    # Generate stream handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG)
    # Create formatter and add it to the handlers
    formatter = logging.Formatter(
                '%(levelname)-5s : %(message)s (%(name)s)')
    sh.setFormatter(formatter)
    module_logger.addHandler(sh)

__all__ = sorted([ 'calc_cable','strahler_index', 'prune_by_strahler','stitch_neurons','arbor_confidence',
            'split_axon_dendrite',  'bending_flow', 'flow_centrality',
            'segregation_index', 'to_dotproduct'])

def arbor_confidence(x, confidences=(1,0.9,0.6,0.4,0.2), inplace=True):
    """ Calculates confidence for each treenode by walking from root to leafs
    starting with a confidence of 1. Each time a low confidence edge is
    encountered the downstream confidence is reduced (see value parameter).

    Parameters
    ----------
    x :                 {CatmaidNeuron, CatmaidNeuronList}
                        Neuron(s) to calculate confidence for.
    confidences :       list of five floats, optional
                        Values by which the confidence of the downstream
                        branche is reduced upon encounter of a 5/4/3/2/1-
                        confidence edges.
    inplace :           bool, optional
                        If False, a copy of the neuron is returned.

    Returns
    -------
    Adds ``arbor_confidence`` column in neuron.nodes.
    """

    def walk_to_leafs( this_node, this_confidence=1 ):
        pbar.update(1)
        while True:
            this_confidence *= confidences[ 5 - nodes.loc[ this_node ].confidence ]
            nodes.loc[ this_node,'arbor_confidence'] = this_confidence

            if len(loc[this_node]) > 1:
                for c in loc[this_node]:
                    walk_to_leafs( c, this_confidence )
                break
            elif len(loc[this_node]) == 0:
                break

            this_node = loc[this_node][0]

    if not isinstance(x, ( core.CatmaidNeuron, core.CatmaidNeuronList )):
        raise TypeError('Unable to process data of type %s' % str(type(x)))

    if isinstance(x, core.CatmaidNeuronList):
        if not inplace:
            res = [ arbor_confidence(n, confidence=confidence, inplace=inplace) for n in x ]
        else:
            return core.CatmaidNeuronList( [ arbor_confidence(n, confidence=confidence, inplace=inplace) for n in x ] )

    if not inplace:
        x = x.copy()

    loc = graph_utils.generate_list_of_childs(x)

    x.nodes['arbor_confidence'] = [None] * x.nodes.shape[0]

    nodes = x.nodes.set_index('treenode_id')
    nodes.loc[x.root,'arbor_confidence'] = 1

    with tqdm(total=len(x.segments),desc='Calc confidence', disable=module_logger.getEffectiveLevel()>=40 ) as pbar:
        for r in x.root:
            for c in loc[r]:
                walk_to_leafs(c)

    x.nodes['arbor_confidence'] = nodes['arbor_confidence'].values

    if not inplace:
        return x

def _calc_dist(v1, v2):
    return math.sqrt(sum(((a - b)**2 for a, b in zip(v1, v2))))


def calc_cable(skdata, smoothing=1, remote_instance=None, return_skdata=False):
    """ Calculates cable length in micrometer (um).

    Parameters
    ----------
    skdata :            {int, str, CatmaidNeuron, CatmaidNeuronList}
                        If skeleton ID (str or in), 3D skeleton data will be
                        pulled from CATMAID server.
    smoothing :         int, optional
                        Use to smooth neuron by downsampling.
                        Default = 1 (no smoothing) .
    remote_instance :   CATMAID instance, optional
                        Pass if skdata is a skeleton ID.
    return_skdata :     bool, optional
                        If True: instead of the final cable length, a dataframe
                        containing the distance to each treenode's parent.

    Returns
    -------
    cable_length
                Cable in micrometers [um]

    skdata
                If return_skdata = True. Neuron object with
                ``nodes.parent_dist`` containing the distances to parent.
    """

    remote_instance = fetch._eval_remote_instance(remote_instance)

    if isinstance(skdata, int) or isinstance(skdata, str):
        skdata = fetch.get_neuron([skdata], remote_instance).loc[0]

    if isinstance(skdata, pd.Series) or isinstance(skdata, core.CatmaidNeuron):
        df = skdata
    elif isinstance(skdata, pd.DataFrame) or isinstance(skdata, core.CatmaidNeuronList):
        if skdata.shape[0] == 1:
            df = skdata.loc[0]
        elif not return_skdata:
            return sum([calc_cable(skdata.loc[i]) for i in range(skdata.shape[0])])
        else:
            return core.CatmaidNeuronList([calc_cable(skdata.loc[i], return_skdata=return_skdata) for i in range(skdata.shape[0])])
    else:
        raise Exception('Unable to interpret data of type', type(skdata))

    # Copy node data too
    df.nodes = df.nodes.copy()

    # Catch single-node neurons
    if df.nodes.shape[0] == 1:
        if return_skdata:
            df.nodes['parent_dist'] = 0
            return df
        else:
            return 0

    if smoothing > 1:
        df = downsample_neuron(df, smoothing)

    if df.nodes.index.name != 'treenode_id':
        df.nodes.set_index('treenode_id', inplace=True)

    # Calculate distance to parent for each node
    nodes = df.nodes[~df.nodes.parent_id.isnull()]
    tn_coords = nodes[['x', 'y', 'z']].reset_index()
    parent_coords = df.nodes.loc[[n for n in nodes.parent_id.tolist()],
        ['x', 'y', 'z']].reset_index()

    # Calculate distances between nodes and their parents
    w = np.sqrt(np.sum(
        (tn_coords[['x', 'y', 'z']] - parent_coords[['x', 'y', 'z']]) ** 2, axis=1))

    df.nodes.reset_index(inplace=True)

    if return_skdata:
        df.nodes['parent_dist'] = [v / 1000 for v in list(w)]
        return df

    # #Remove nan value (at parent node) and return sum of all distances
    return np.sum(w[np.logical_not(np.isnan(w))]) / 1000


def to_dotproduct(x):
    """ Converts a neuron's neurites into dotproducts consisting of a point
    and a vector. This works by (1) finding the center between child->parent
    treenodes and (2) getting the vector between them. Also returns the length
    of the vector.

    Parameters
    ----------
    x :         {CatmaidNeuron}
                Single neuron

    Returns
    -------
    pandas.DataFrame
            DataFrame in which each row represents a segments between two
            treenodes.

            >>> df
                point  vector  vec_length
            1
            2
            3

    Examples
    --------
    >>> x = pymaid.get_neurons(16)
    >>> dps = pymaid.to_dotproduct(x)
    >>> # Get array of all locations
    >>> locs = numpy.vstack(dps.point.values)

    See Also
    --------
    pymaid.CatmaidNeuron.dps

    """

    if isinstance(x, core.CatmaidNeuronList):
        if x.shape[0] == 1:
            x = x[0]
        else:
            raise ValueError('Please pass only single CatmaidNeurons')

    if not isinstance(x, core.CatmaidNeuron ):
        raise ValueError('Can only process CatmaidNeurons')

    # First, get a list of child -> parent locs (exclude root node!)
    tn_locs = x.nodes[ ~x.nodes.parent_id.isnull() ][['x','y','z']].values
    pn_locs = x.nodes.set_index('treenode_id').loc[ x.nodes[ ~x.nodes.parent_id.isnull() ].parent_id ][['x','y','z']].values

    # Get centers between each pair of locs
    centers = tn_locs + ( pn_locs - tn_locs ) / 2

    # Get vector between points
    vec = pn_locs - tn_locs

    dps = pd.DataFrame( [ [ c, v] for c,v in zip(centers,vec) ], columns=['point','vector'] )

    # Add length of vector (for convenience)
    dps['vec_length'] = (dps.vector ** 2).apply(sum).apply(math.sqrt)

    return dps


def strahler_index(skdata, inplace=True, method='standard'):
    """ Calculates Strahler Index. Starts with index of 1 at each leaf. At
    forks with varying incoming strahler index, the highest index
    is continued. At forks with the same incoming strahler index, highest
    index + 1 is continued. Starts with end nodes, then works its way from
    branch nodes to branch nodes up to root node

    Parameters
    ----------
    skdata :      {CatmaidNeuron, CatmaidNeuronList}
                  E.g. from  ``pymaid.get_neuron()``.
    inplace :     bool, optional
                  If False, a copy of original skdata is returned.
    method :      {'standard','greedy'}, optional
                  Method used to calculate strahler indices: 'standard' will
                  use the method described above; 'greedy' will always
                  increase the index at converging branches whether these
                  branches have the same index or not. This is useful e.g. if
                  you want to cut the neuron at the first branch point.

    Returns
    -------
    skdata
                  With new column ``skdata.nodes.strahler_index``
    """

    module_logger.info('Calculating Strahler indices...')

    start_time = time.time()

    if isinstance(skdata, pd.Series) or isinstance(skdata, core.CatmaidNeuron):
        df = skdata
    elif isinstance(skdata, pd.DataFrame) or isinstance(skdata, core.CatmaidNeuronList):
        if skdata.shape[0] == 1:
            df = skdata.loc[0]
        else:
            res = []
            for i in trange(0, skdata.shape[0] ):
                res.append(  strahler_index(skdata.loc[i], inplace=inplace, method=method ) )

            if not inplace:
                return core.CatmaidNeuronList( res )
            else:
                return

    if not inplace:
        df = df.copy()

    # Make sure dataframe is not indexed by treenode_id for preparing lists
    df.nodes.reset_index(inplace=True, drop=True)

    # Find branch, root and end nodes
    if 'type' not in df.nodes:
        classify_nodes(df)

    end_nodes = df.nodes[df.nodes.type == 'end'].treenode_id.tolist()
    branch_nodes = df.nodes[df.nodes.type == 'branch'].treenode_id.tolist()
    root = df.nodes[df.nodes.type == 'root'].treenode_id.tolist()

    # Generate dicts for childs and parents
    list_of_childs = graph_utils.generate_list_of_childs(skdata)
    #list_of_parents = { n[0]:n[1] for n in skdata[0] }

    # Reindex according to treenode_id
    if df.nodes.index.name != 'treenode_id':
        df.nodes.set_index('treenode_id', inplace=True)

    strahler_index = {n: None for n in list_of_childs if n != None}

    starting_points = end_nodes

    nodes_processed = []

    while starting_points:
        module_logger.debug('New starting point. Remaining: %i' %
                            len(starting_points))
        new_starting_points = []
        starting_points_done = []

        for i, en in enumerate(starting_points):
            this_node = en

            module_logger.debug('%i of %i ' % (i, len(starting_points)))

            # Calculate index for this branch
            previous_indices = []
            for child in list_of_childs[this_node]:
                previous_indices.append(strahler_index[child])

            if len(previous_indices) == 0:
                this_branch_index = 1
            elif len(previous_indices) == 1:
                this_branch_index = previous_indices[0]
            elif previous_indices.count(max(previous_indices)) >= 2 or method == 'greedy':
                this_branch_index = max(previous_indices) + 1
            else:
                this_branch_index = max(previous_indices)

            nodes_processed.append(this_node)
            starting_points_done.append(this_node)

            # Now walk down this spine
            # Find parent
            spine = [this_node]

            #parent_node = list_of_parents [ this_node ]
            parent_node = df.nodes.loc[this_node,'parent_id']

            while parent_node not in branch_nodes and parent_node != None:
                this_node = parent_node
                parent_node = None

                spine.append(this_node)
                nodes_processed.append(this_node)

                # Find next parent
                try:
                    parent_node = df.nodes.loc[this_node,'parent_id']
                except:
                    # Will fail if at root (no parent)
                    break

            strahler_index.update({n: this_branch_index for n in spine})

            # The last this_node is either a branch node or the root
            # If a branch point: check, if all its childs have already been
            # processed
            if parent_node != None:
                node_ready = True
                for child in list_of_childs[parent_node]:
                    if child not in nodes_processed:
                        node_ready = False

                if node_ready is True and parent_node != None:
                    new_starting_points.append(parent_node)

        # Remove those starting_points that were successfully processed in this
        # run before the next iteration
        for node in starting_points_done:
            starting_points.remove(node)

        # Add new starting points
        starting_points += new_starting_points

    df.nodes.reset_index(inplace=True)

    df.nodes['strahler_index'] = [strahler_index[n]
                                  for n in df.nodes.treenode_id.tolist()]

    module_logger.debug('Done in %is' % round(time.time() - start_time))

    if not inplace:
        return df


def prune_by_strahler(x, to_prune=range(1, 2), reroot_soma=True, inplace=False, force_strahler_update=False, relocate_connectors=False):
    """ Prune neuron based on strahler order.

    Parameters
    ----------
    x :             {core.CatmaidNeuron, core.CatmaidNeuronList}
    to_prune :      {int, list, range}, optional
                    Strahler indices to prune:

                      (1) ``to_prune=1`` removes all leaf branches
                      (2) ``to_prune=[1,2]`` removes indices 1 and 2
                      (3) ``to_prune=range(1,4)`` removes indices 1, 2 and 3
                      (4) ``to_prune=s-1`` removes everything but the highest
                          index
    reroot_soma :   bool, optional
                    If True, neuron will be rerooted to its soma
    inplace :       bool, optional
                    If False, pruning is performed on copy of original neuron
                    which is then returned.
    relocate_connectors : bool, optional
                          If True, connectors on removed treenodes will be
                          reconnected to the closest still existing treenode.
                          Works only in child->parent direction.


    Returns
    -------
    pymaid.CatmaidNeuron/List
                    Pruned neuron.
    """

    if isinstance(x, core.CatmaidNeuron):
        neuron = x
    elif isinstance(x, core.CatmaidNeuronList):
        temp = [prune_by_strahler(
            n, to_prune=to_prune, inplace=inplace) for n in x]
        if not inplace:
            return core.CatmaidNeuronList(temp, x._remote_instance)
        else:
            return

    # Make a copy if necessary before making any changes
    if not inplace:
        neuron = neuron.copy()

    if reroot_soma and neuron.soma:
        neuron.reroot(neuron.soma)

    if 'strahler_index' not in neuron.nodes or force_strahler_update:
        strahler_index(neuron)

    # Prepare indices
    if isinstance(to_prune, int) and to_prune < 0:
        to_prune = range(1, neuron.nodes.strahler_index.max() + (to_prune + 1))
    elif isinstance(to_prune, int):
        to_prune = [to_prune]
    elif isinstance(to_prune, range):
        to_prune = list(to_prune)

    # Prepare parent dict if needed later
    if relocate_connectors:
        parent_dict = { tn.treenode_id : tn.parent_id for tn in neuron.nodes.itertuples() }

    neuron.nodes = neuron.nodes[
        ~neuron.nodes.strahler_index.isin(to_prune)].reset_index(drop=True)

    if not relocate_connectors:
        neuron.connectors = neuron.connectors[neuron.connectors.treenode_id.isin(
            neuron.nodes.treenode_id.tolist())].reset_index(drop=True)
    else:
        remaining_tns = neuron.nodes.treenode_id.tolist()
        for cn in neuron.connectors[~neuron.connectors.treenode_id.isin(neuron.nodes.treenode_id.tolist())].itertuples():
            this_tn = parent_dict[ cn.treenode_id ]
            while True:
                if this_tn in remaining_tns:
                    break
                this_tn = parent_dict[ this_tn ]
            neuron.connectors.loc[cn.Index,'treenode_id'] = this_tn

    # Reset indices of node and connector tables (important for igraph!)
    neuron.nodes.reset_index(inplace=True,drop=True)
    neuron.connectors.reset_index(inplace=True,drop=True)

    # Theoretically we can end up with disconnected pieces, i.e. with more than 1 root node
    # We have to fix the nodes that lost their parents
    neuron.nodes.loc[ ~neuron.nodes.parent_id.isin( neuron.nodes.treenode_id.tolist() ), 'parent_id' ] = None

    # Remove temporary attributes
    neuron._clear_temp_attr()

    if not inplace:
        return neuron
    else:
        return


def split_axon_dendrite(x, method='centrifugal', primary_neurite=True, reroot_soma=True, return_point=False ):
    """ This function tries to split a neuron into axon, dendrite and primary
    neurite. The result is highly depending on the method and on your
    neuron's morphology and works best for "typical" neurons, i.e. those where
    the primary neurite branches into axon and dendrites.
    See :func:`~pymaid.flow_centrality` for details on the flow
    centrality algorithm.

    Parameters
    ----------
    x :                 CatmaidNeuron
                        Neuron to split into axon, dendrite and primary neurite
    method :            {'centrifugal','centripetal','sum', 'bending'}, optional
                        Type of flow centrality to use to split the neuron.
                        There are four flavors: the first three refer to
                        :func:`~pymaid.flow_centrality`, the last
                        refers to :func:`~pymaid.bending_flow`.

                        Will try using stored centrality, if possible.
    primary_neurite :   bool, optional
                        If True and the split point is at a branch point, will
                        split into axon, dendrite and primary neurite.
    reroot_soma :       bool, optional
                        If True, will make sure neuron is rooted to soma if at
                        all possible.
    return_point :      bool, optional
                        If True, will only return treenode ID of the node at which
                        to split the neuron.

    Returns
    -------
    CatmaidNeuronList
        Contains Axon, Dendrite and primary neurite

    Examples
    --------
    >>> x = pymaid.get_neuron(123456)
    >>> split = pymaid.split_axon_dendrite(x, method='centrifugal', reroot_soma=True)
    >>> split
    <class 'pymaid.CatmaidNeuronList'> of 3 neurons
                          neuron_name skeleton_id  n_nodes  n_connectors
    0  neuron 123457_primary_neurite          16      148             0
    1             neuron 123457_axon          16     9682          1766
    2         neuron 123457_dendrite          16     2892           113
    >>> # Plot in their respective colors
    >>> for n in split:
    >>>   n.plot3d(color=self.color)

    """

    if isinstance(x, core.CatmaidNeuronList) and len(x) == 1:
        x = x[0]

    if not isinstance(x, core.CatmaidNeuron):
        raise TypeError('Can only process a single CatmaidNeuron')

    if method not in ['centrifugal','centripetal','sum','bending']:
        raise ValueError('Unknown parameter for mode: {0}'.format(mode))

    if x.soma and x.soma not in x.root and reroot_soma:
        x.reroot(x.soma)

    # Calculate flow centrality if necessary
    try:
        last_method = x.centrality_method
    except:
        last_method = None

    if last_method != method:
        if method == 'bending':
            _ = bending_flow(x)
        else:
            _ = flow_centrality(x, mode = method)

    #Make copy, so that we don't screw things up
    x = x.copy()

    module_logger.info('Splitting neuron #{0} by flow centrality'.format(x.skeleton_id))

    # Now get the node point with the highest flow centrality.
    cut = x.nodes[ (x.nodes.flow_centrality == x.nodes.flow_centrality.max()) ].treenode_id.tolist()

    # If there is more than one point we need to get one closest to the soma (root)
    cut = sorted(cut, key = lambda y : graph_utils.dist_between( x.graph, y, x.root[0] ) )[0]

    if return_point:
        return cut

    # If cut node is a branch point, we will try cutting off main neurite
    if x.graph.degree(cut) > 2 and primary_neurite:
        rest, primary_neurite = cut_neuron( x, next( x.graph.successors(cut) ) )
        # Change name and color
        primary_neurite.neuron_name = x.neuron_name + '_primary_neurite'
        primary_neurite.color = (0,255,0)
    else:
        rest = x
        primary_neurite = None

    # Next, cut the rest into axon and dendrite
    a, b = graph_utils.cut_neuron( rest, cut )

    # Figure out which one is which by comparing number of presynapses
    if a.n_presynapses < b.n_presynapses:
        dendrite, axon = a, b
    else:
        dendrite, axon = b, a

    axon.neuron_name = x.neuron_name + '_axon'
    dendrite.neuron_name = x.neuron_name + '_dendrites'

    #Change colors
    axon.color = (255,0,0)
    dendrite.color = (0,0,255)

    if primary_neurite:
        return core.CatmaidNeuronList([ primary_neurite, axon, dendrite ])
    else:
        return core.CatmaidNeuronList([ axon, dendrite ])

def segregation_index(x, centrality_method='centrifugal'):
    """ Calculates segregation index (SI) from Schneider-Mizell et al. (eLife,
    2016) as metric for how polarized a neuron is. SI of 1 indicates total
    segregation of inputs and outputs into dendrites and axon, respectively.
    SI of 0 indicates homogeneous distribution.

    Parameters
    ----------
    x :                 {CatmaidNeuron, CatmaidNeuronList}
                        Neuron to calculate segregation index (SI). If a
                        NeuronList is provided, will assume that this is a
                        split.
    centrality_method : {'centrifugal','centripetal','sum', 'bending'}, optional
                        Type of flow centrality to use to split the neuron.
                        There are four flavors: the first three refer to
                        :func:`~pymaid.flow_centrality`, the last
                        refers to :func:`~pymaid.bending_flow`.

                        Will try using stored centrality, if possible.

    Notes
    -----
    From Schneider-Mizell et al. (2016): "Note that even a modest amount of
    mixture (e.g. axo-axonic inputs) corresponds to values near H = 0.5–0.6
    (Figure 7—figure supplement 1). We consider an unsegregated neuron
    (H ¡ 0.05) to be purely dendritic due to their anatomical similarity with
    the dendritic domains of those segregated neurons that have dendritic
    outputs."

    Returns
    -------
    H :                 float
                        Segregation Index (SI)
    """

    if not isinstance(x, (core.CatmaidNeuron,core.CatmaidNeuronList)):
        raise ValueError('Must pass CatmaidNeuron or CatmaidNeuronList, not {0}'.format(type(x)))

    if not isinstance(x, core.CatmaidNeuronList):
        # Get the branch point with highest flow centrality
        split_point = split_axon_dendrite(x, reroot_soma=True, return_point=True )

        # Now make a virtual split (downsampled neuron to speed things up)
        temp = x.copy()
        temp.downsample(10000)

        # Get one of its children
        child = temp.nodes[ temp.nodes.parent_id == split_point ].treenode_id.tolist()[0]

        # This will leave the proximal split with the primary neurite but
        # since that should not have synapses, we don't care at this point.
        x = core.CatmaidNeuronList( cut_neuron( temp, child ) )

    # Calculate entropy for each fragment
    entropy = []
    for n in x:
        p = n.n_postsynapses / n.n_connectors

        if 0 < p < 1:
            S = - ( p * math.log( p ) + ( 1 - p ) * math.log( 1 - p ) )
        else:
            S = 0

        entropy.append(S)

    # Calc entropy between fragments
    S = 1 / sum(x.n_connectors) * sum( [  e * x[i].n_connectors for i,e in enumerate(entropy) ] )

    # Normalize to entropy in whole neuron
    p_norm = sum(x.n_postsynapses) / sum(x.n_connectors)
    if 0 < p_norm < 1:
        S_norm = - ( p_norm * math.log( p_norm ) + ( 1 - p_norm ) * math.log( 1 - p_norm ) )
        H = 1 - S / S_norm
    else:
        S_norm = 0
        H = 0

    return H

def bending_flow(x, polypre=False):
    """ Variation of the algorithm for calculating synapse flow from
    Schneider-Mizell et al. (eLife, 2016).

    The way this implementation works is by iterating over each branch point
    and counting the number of pre->post synapse paths that "flow" from one
    child branch to the other(s).

    Parameters
    ----------
    x :         {CatmaidNeuron, CatmaidNeuronList}
                Neuron(s) to calculate bending flow for
    polypre :   bool, optional
                Whether to consider the number of presynapses as a multiple of
                the numbers of connections each makes. Attention: this works
                only if all synapses have been properly annotated.

    Notes
    -----
    This is algorithm appears to be more reliable than synapse flow
    centrality for identifying the main branch point for neurons that have
    only partially annotated synapses.

    See Also
    --------
    :func:`~pymaid.flow_centrality`
            Calculate synapse flow centrality after Schneider-Mizell et al
    :func:`~pymaid.segregation_score`
            Uses flow centrality to calculate segregation score (polarity)
    :func:`~pymaid.split_axon_dendrite`
            Split the neuron into axon, dendrite and primary neurite.

    Returns
    -------
    Adds a new column 'flow_centrality' to ``x.nodes``. Branch points only!

    """
    module_logger.info('Calculating bending flow centrality for neuron #{0}'.format(x.skeleton_id))

    start_time = time.time()

    if not isinstance(x, (core.CatmaidNeuron,core.CatmaidNeuronList)):
        raise ValueError('Must pass CatmaidNeuron or CatmaidNeuronList, not {0}'.format(type(x)))

    if isinstance(x, core.CatmaidNeuronList):
        return [ bending_flow(n, mode=mode, polypre=polypre, ) for n in x ]

    if x.soma and x.soma not in x.root:
        module_logger.warning('Neuron {0} is not rooted to its soma!'.format(x.skeleton_id))

    # We will be processing a super downsampled version of the neuron to speed up calculations
    current_level = module_logger.level
    module_logger.setLevel('ERROR')
    y = x.copy()
    y.downsample(1000000)
    module_logger.setLevel(current_level)

    if polypre:
        # Get details for all presynapses
        cn_details = fetch.get_connector_details( y.connectors[ y.connectors.relation==0 ] )

    # Get list of nodes with pre/postsynapses
    pre_node_ids = y.connectors[ y.connectors.relation==0 ].treenode_id.values
    post_node_ids = y.connectors[ y.connectors.relation==1 ].treenode_id.values

    # Get list of branch_points
    bp_node_ids = y.nodes[ y.nodes.type == 'branch' ].treenode_id.values.tolist()
    # Add root if it is also a branch point
    for root in y.root:
        if y.graph.degree( root ) > 1:
            bp_node_ids += list( root )

    # Get list of childs of each branch point
    bp_childs = { t : [ e[0] for e in y.graph.in_edges(t) ] for t in bp_node_ids }
    childs = [ tn for l in bp_childs.values() for tn in l ]

    # Get number of pre/postsynapses distal to each branch's childs
    distal_pre = graph_utils.distal_to( y, pre_node_ids, childs )
    distal_post = graph_utils.distal_to( y, post_node_ids, childs )

    # Multiply columns (presynapses) by the number of postsynaptically connected nodes
    if polypre:
        # Map vertex ID to number of postsynaptic nodes (avoid 0)
        distal_pre *= [ max( 1, len( cn_details[ cn_details.presynaptic_to_node == n ].postsynaptic_to_node.sum() ) ) for n in distal_pre.columns ]

    # Sum up axis - now each row represents the number of pre/postsynapses distal to that node
    distal_pre = distal_pre.T.sum(axis=1)
    distal_post = distal_post.T.sum(axis=1)

    # Now go over all branch points and check flow between branches (centrifugal) vs flow from branches to root (centripetal)
    flow = { bp : 0 for bp in bp_childs }
    for bp in bp_childs:
        # We will use left/right to label the different branches here (even if there is more than two)
        for left, right in itertools.permutations( bp_childs[bp], r=2 ):
            flow[bp] += distal_post.loc[ left ] * distal_pre.loc[ right ]

    # Set flow centrality to None for all nodes
    x.nodes['flow_centrality'] = None

    # Change index to treenode_id
    x.nodes.set_index('treenode_id', inplace=True)

    # Add flow (make sure we use igraph of y to get node ids!)
    x.nodes.loc[ flow.keys(), 'flow_centrality' ] = list(flow.values())

    # Add little info on method used for flow centrality
    x.centrality_method = 'bending'

    x.nodes.reset_index(inplace=True)

    module_logger.debug('Total time for bending flow calculation: {0}s'.format( round(time.time() - start_time ) ))

    return


def flow_centrality(x, mode = 'centrifugal', polypre=False ):
    """ Implementation of the algorithm for calculating flow centrality.

    Parameters
    ----------
    x :         {CatmaidNeuron, CatmaidNeuronList}
                Neuron(s) to calculate flow centrality for
    mode :      {'centrifugal','centripetal','sum'}, optional
                Type of flow centrality to calculate. There are three flavors::
                (1) centrifugal, which counts paths from proximal inputs to distal outputs
                (2) centripetal, which counts paths from distal inputs to proximal outputs
                (3) the sum of both
    polypre :   bool, optional
                Whether to consider the number of presynapses as a multiple of
                the numbers of connections each makes. Attention: this works
                only if all synapses have been properly annotated (i.e. all
                postsynaptic sites).

    Notes
    -----
    From Schneider-Mizell et al. (2016): "We use flow centrality for
    four purposes. First, to split an arbor into axon and dendrite at the
    maximum centrifugal SFC, which is a preliminary step for computing the
    segregation index, for expressing all kinds of connectivity edges (e.g.
    axo-axonic, dendro-dendritic) in the wiring diagram, or for rendering the
    arbor in 3d with differently colored regions. Second, to quantitatively
    estimate the cable distance between the axon terminals and dendritic arbor
    by measuring the amount of cable with the maximum centrifugal SFC value.
    Third, to measure the cable length of the main dendritic shafts using
    centripetal SFC, which applies only to insect neurons with at least one
    output syn- apse in their dendritic arbor. And fourth, to weigh the color
    of each skeleton node in a 3d view, providing a characteristic signature of
    the arbor that enables subjective evaluation of its identity."

    Losely based on Alex Bate's implemention in
    https://github.com/alexanderbates/catnat.

    Pymaid uses the equivalent of ``mode='sum'`` and ``polypre=True``.

    See Also
    --------
    :func:`~pymaid.bending_flow`
            Variation of flow centrality: calculates bending flow.
    :func:`~pymaid.segregation_index`
            Calculates segregation score (polarity) of a neuron
    :func:`~pymaid.flow_centrality_split`
            Tries splitting a neuron into axon, dendrite and primary neurite.


    Returns
    -------
    Adds a new column 'flow_centrality' to ``x.nodes``. Ignores non-synapse
    holding segment nodes!

    """

    module_logger.info('Calculating flow centrality for neuron #{0}'.format(x.skeleton_id))

    start_time = time.time()

    if mode not in ['centrifugal','centripetal','sum']:
        raise ValueError('Unknown parameter for mode: {0}'.format(mode))

    if not isinstance(x, (core.CatmaidNeuron,core.CatmaidNeuronList)):
        raise ValueError('Must pass CatmaidNeuron or CatmaidNeuronList, not {0}'.format(type(x)))

    if isinstance(x, core.CatmaidNeuronList):
        return [ flow_centrality(n, mode=mode, polypre=polypre, ) for n in x ]

    if x.soma and x.soma not in x.root:
        module_logger.warning('Neuron {0} is not rooted to its soma!'.format(x.skeleton_id))

    # We will be processing a super downsampled version of the neuron to speed up calculations
    current_level = module_logger.level
    module_logger.setLevel('ERROR')
    y = x.copy()
    y.downsample(1000000)
    module_logger.setLevel(current_level)

    if polypre:
        # Get details for all presynapses
        cn_details = fetch.get_connector_details( y.connectors[ y.connectors.relation==0 ] )

    # Get list of nodes with pre/postsynapses
    pre_node_ids = y.connectors[ y.connectors.relation==0 ].treenode_id.unique()
    post_node_ids = y.connectors[ y.connectors.relation==1 ].treenode_id.unique()
    total_pre = len(pre_node_ids)
    total_post = len(post_node_ids)

    # Get list of points to calculate flow centrality for:
    # branches and nodes with synapses
    calc_node_ids = y.nodes[ (y.nodes.type == 'branch') | (y.nodes.treenode_id.isin(y.connectors.treenode_id) ) ].treenode_id.values

    # Get number of pre/postsynapses distal to each branch's childs
    distal_pre = graph_utils.distal_to( y, pre_node_ids, calc_node_ids  )
    distal_post = graph_utils.distal_to( y, post_node_ids, calc_node_ids )

    # Multiply columns (presynapses) by the number of postsynaptically connected nodes
    if polypre:
        # Map vertex ID to number of postsynaptic nodes (avoid 0)
        distal_pre *= [ max( 1, len( cn_details[ cn_details.presynaptic_to_node == n ].postsynaptic_to_node.sum() ) ) for n in distal_pre.columns ]
        # Also change total_pre as accordingly
        total_pre = sum( [ max( 1, len(row) ) for row in cn_details.postsynaptic_to_node.tolist() ] )

    # Sum up axis - now each row represents the number of pre/postsynapses that are distal to that node
    distal_pre = distal_pre.T.sum(axis=1)
    distal_post = distal_post.T.sum(axis=1)

    if mode != 'centripetal':
        # Centrifugal is the flow from all non-distal postsynapses to all distal presynapses
        centrifugal = { n : ( total_post - distal_post[n] ) * distal_pre[n] for n in calc_node_ids }

    if mode != 'centrifugal':
        # Centripetal is the flow from all distal postsynapses to all non-distal presynapses
        centripetal = { n : distal_post[n] * ( total_post - distal_pre[n]) for n in calc_node_ids }

    # Set flow centrality to None for all nodes
    x.nodes['flow_centrality'] = None

    # Change index to treenode_id
    x.nodes.set_index('treenode_id', inplace=True)

    # Now map this onto our neuron
    if mode == 'centrifugal':
        res = list( centrifugal.values() )
    elif mode == 'centripetal':
        res = list( centripetal.values() )
    elif mode == 'sum':
        res = np.array( list(centrifugal.values()) ) + np.array( list(centripetal.values()) )

    # Add results
    x.nodes.loc[ list( centrifugal.keys() ), 'flow_centrality' ] = res

    # Add little info on method/mode used for flow centrality
    x.centrality_method = mode

    x.nodes.reset_index(inplace=True)

    module_logger.debug('Total time for SFC calculation: {0}s'.format( round(time.time() - start_time ) ))

    return


def stitch_neurons( *x, tn_to_stitch=None, method='ALL'):
    """ Stitch multiple neurons together.

    Notes
    -----
    The first neuron provided will be the master neuron. Unless treenode IDs
    are provided via ``tn_to_stitch``, neurons will be stitched at the
    closest point.

    Parameters
    ----------
    x :                 CatmaidNeuron/CatmaidNeuronList
                        Neurons to stitch.
    tn_to_stitch :      List of treenode IDs, optional
                        If provided, these treenodes will be preferentially
                        used to stitch neurons together. If there are more
                        than two possible treenodes for a single stitching
                        operation, the two closest are used.
    method :            {'LEAFS','ALL','NONE'}, optional
                        Set stitching method:
                            (1) 'LEAFS': only leaf (including root) nodes will
                                be considered for stitching
                            (2) 'ALL': all treenodes are considered
                            (3) 'NONE': node and connector tables will simply
                                be combined. Use this if your neurons consists
                                of fragments with multiple roots.

    Returns
    -------
    core.CatmaidNeuron
    """

    if method not in ['LEAFS', 'ALL', 'NONE', None]:
        raise ValueError('Unknown method: %s' % str(method))

    # Compile list of individual neurons
    neurons = []
    for n in x:
        if not isinstance(n, (core.CatmaidNeuron, core.CatmaidNeuronList) ):
            raise TypeError( 'Unable to stitch non-CatmaidNeuron objects' )
        elif isinstance(n, core.CatmaidNeuronList):
            neurons += n.neurons
        else:
            neurons.append(n)

    #Use copies of the original neurons!
    neurons = [ n.copy() for n in neurons if isinstance(n, core.CatmaidNeuron )]

    if len(neurons) < 2:
        module_logger.warning('Need at least 2 neurons to stitch, found %i' % len(neurons))
        return neurons[0]

    module_logger.debug('Stitching %i neurons...' % len(neurons))

    stitched_n = neurons[0]

    # If method is none, we can just merge the data tables
    if method == 'NONE' or method == None:
        stitched_n.nodes = pd.concat( [ n.nodes for n in neurons ], ignore_index=True )
        stitched_n.connectors = pd.concat( [ n.connectors for n in neurons ], ignore_index=True )
        stitched_n.tags = {}
        for n in neurons:
            stitched_n.tags.update( n.tags )

        #Reset temporary attributes of our final neuron
        stitched_n._clear_temp_attr()

        return stitched_n

    if tn_to_stitch and not isinstance(tn_to_stitch, (list, np.ndarray)):
        tn_to_stitch = [ tn_to_stitch ]
        tn_to_stitch = [ str(tn) for tn in tn_to_stitch ]

    for nB in neurons[1:]:
        #First find treenodes to connect
        if tn_to_stitch and set(tn_to_stitch) & set(stitched_n.nodes.treenode_id) and set(tn_to_stitch) & set(nB.nodes.treenode_id):
            treenodesA = stitched_n.nodes.set_index('treenode_id').loc[ tn_to_stitch ].reset_index()
            treenodesB = nB.nodes.set_index('treenode_id').loc[ tn_to_stitch ].reset_index()
        elif method == 'LEAFS':
            treenodesA = stitched_n.nodes[ stitched_n.nodes.type.isin(['end','root']) ].reset_index()
            treenodesB = nB.nodes[ nB.nodes.type.isin(['end','root']) ].reset_index()
        else:
            treenodesA = stitched_n.nodes
            treenodesB = nB.nodes

        #Calculate pairwise distances
        dist = scipy.spatial.distance.cdist( treenodesA[['x','y','z']].values,
                                              treenodesB[['x','y','z']].values,
                                              metric='euclidean' )

        #Get the closest treenodes
        tnA = treenodesA.loc[ dist.argmin(axis=0)[0] ].treenode_id
        tnB = treenodesB.loc[ dist.argmin(axis=1)[0] ].treenode_id

        module_logger.info('Stitching treenodes %s and %s' % ( str(tnA), str(tnB) ))

        #Reroot neuronB onto the node that will be stitched
        nB.reroot( tnB )

        #Change neuronA root node's parent to treenode of neuron B
        nB.nodes.loc[ nB.nodes.parent_id.isnull(), 'parent_id' ] = tnA

        #Add nodes, connectors and tags onto the stitched neuron
        stitched_n.nodes = pd.concat( [ stitched_n.nodes, nB.nodes ], ignore_index=True )
        stitched_n.connectors = pd.concat( [ stitched_n.connectors, nB.connectors ], ignore_index=True )
        stitched_n.tags.update( nB.tags )

    #Reset temporary attributes of our final neuron
    stitched_n._clear_temp_attr()

    return stitched_n


