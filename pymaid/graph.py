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

""" Collection of tools to turn CATMAID neurons into Graph representations.
"""

import numpy as np
import pandas as pd

import logging

from pymaid import core, fetch

import networkx as nx 

try:
    import igraph
except:
    igraph = None

# Set up logging
module_logger = logging.getLogger(__name__)
module_logger.setLevel(logging.DEBUG)
if len( module_logger.handlers ) == 0:
    # Generate stream handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    # Create formatter and add it to the handlers
    formatter = logging.Formatter(
                '%(levelname)-5s : %(message)s (%(name)s)')
    sh.setFormatter(formatter)
    module_logger.addHandler(sh)

__all__ = sorted(['network2nx','network2igraph','neuron2igraph','dist_from_root','neuron2nx'])


def network2nx(x, remote_instance=None, threshold=1):
    """ Generates NetworkX graph for neuron connectivity.

    Parameters
    ----------
    x                  
                        Catmaid Neurons as:
                         1. list of skeleton IDs (int or str)
                         2. list of neuron names (str, exact match)
                         3. annotation(s): e.g. 'annotation:PN right'
                         4. CatmaidNeuronList object
                         5. Adjacency matrix (pd.DataFrame, rows=sources,
                            columns=targets)
    remote_instance :   CATMAID instance, optional 
                        Either pass directly to function or define globally 
                        as 'remote_instance'.
    threshold :         int, optional
                        Connections weaker than this will be excluded .

    Returns
    ------- 
    networkx.DiGraph
                        NetworkX representation of the network.

    Examples
    --------
    >>> import matplotlib.pyplot as plt
    >>> import networkx as nx
    >>> import numpy as np
    >>> g = pymaid.network2graph('annotation:large network')
    >>> # Plot with default settings
    >>> nx.draw(g)
    >>> plt.show()
    >>> # Plot with neuron names
    >>> labels = nx.get_node_attributes(g, 'neuron_name')  
    >>> nx.draw(g, labels=labels, with_labels=True)
    >>> plt.show()
    >>> # Plot with layout
    >>> layout = nx.circular_layout(g)
    >>> nx.draw(g, pos=layout)
    >>> plt.show()
    >>> # Plot with edge weights
    >>> nx.draw_networkx_nodes(g, pos=layout)
    >>> weight = np.array( list( nx.get_edge_attributes(g, 'weight').values() ) )
    >>> nx.draw_networkx_edges(g, pos=layout, width=weight/50)
    >>> plt.show()

    """

    if isinstance(x, (core.CatmaidNeuronList, list, np.ndarray, str )):
        remote_instance = fetch._eval_remote_instance(remote_instance)
        skids = fetch.eval_skids(x, remote_instance=remote_instance)                       

        # Fetch edges
        edges = fetch.get_edges(skids, remote_instance=remote_instance)
        # Reformat into networkx format
        edges = [[ str(e.source_skid), str(e.target_skid), {'weight': e.weight} ]
                          for e in edges[edges.weight >= threshold].itertuples()]        
    elif isinstance(x, pd.DataFrame):        
        skids = list(set( x.columns.tolist() + x.index.tolist() ))
        # Generate edge list
        edges = [ [ str(s), str(t), { 'weight': x.loc[s,t] } ] for s in x.index.values for t in x.columns.values if x.loc[s,t] >= threshold ]
    else:
        raise ValueError('Unable to process data of type "{0}"'.format(type(x)))

    # Generate node dictionary
    names = fetch.get_names(skids, remote_instance=remote_instance)  
    nodes = [ [ str(s), { 'neuron_name' : names[s] } ] for s in skids ]

    # Generate graph and assign custom properties
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    g.add_edges_from(edges)

    return g
    

def network2igraph(x, remote_instance=None, threshold=1):
    """ Generates iGraph graph for neuron connectivity. Requires iGraph to be
    installed.

    Parameters
    ----------
    x                  
                        Catmaid Neurons as:
                         1. list of skeleton IDs (int or str)
                         2. list of neuron names (str, exact match)
                         3. annotation(s): e.g. 'annotation:PN right'
                         4. CatmaidNeuronList object
                         5. Adjacency matrix (pd.DataFrame, rows=sources,
                            columns=targets)
    remote_instance :   CATMAID instance, optional 
                        Either pass directly to function or define globally 
                        as 'remote_instance'.
    threshold :         int, optional
                        Connections weaker than this will be excluded .

    Returns
    ------- 
    igraph.Graph(directed=True)
                        NetworkX representation of the network.

    Examples
    --------
    >>> import pymaid
    >>> import igraph
    >>> g = pymaid.network2igraph('annotation:large network', remote_instance=rm)
    >>> # Plot graph
    >>> igraph.plot(g)
    >>> # Plot with edge width
    >>> igraph.plot(g, **{'edge_width': [ w/10 for w in g.es['weight'] ] })
    >>> # Plot with edge label
    >>> igraph.plot(g, **{'edge_label': g.es['weight'] })
    >>> # Save as graphml to import into e.g. Cytoscape
    >>> g.save('graph.graphml')

    """
    if igraph == None:
        raise ImportError('igraph must be installed to use this function.')

    if isinstance(x, (core.CatmaidNeuronList, list, np.ndarray, str )):
        remote_instance = fetch._eval_remote_instance(remote_instance)
        skids = fetch.eval_skids(x, remote_instance=remote_instance) 

        indices = { int(s): i for i,s in enumerate(skids) }

        # Fetch edges
        edges = fetch.get_edges(skids, remote_instance=remote_instance)

        # Reformat into igraph format
        edges_by_index = [[indices[e.source_skid], indices[e.target_skid]]
                          for e in edges[edges.weight >= threshold].itertuples()]
        weight = edges[ edges.weight >= threshold ].weight.tolist()   
    elif isinstance(x, pd.DataFrame):
        skids = list(set( x.columns.tolist() + x.index.tolist() ))
        # Generate edge list
        edges = [ [i,j] for i in x.index.tolist() for j in x.columns.tolist() if x.loc[i,j] >= threshold ]
        edges_by_index = [ [ skids.index(e[0]), skids.index(e[1]) ] for e in edges ]
        weight = [ x.loc[i,j] for i in range( x.shape[0] ) for j in range( x.shape[1]) if x.loc[i,j] >= threshold ]
    else:
        raise ValueError('Unable to process data of type "{0}"'.format(type(x)))

    # Generate igraph and assign custom properties
    g = igraph.Graph(directed=True)
    g.add_vertices(len(skids))
    g.add_edges(edges_by_index)

    g.vs['node_id'] = skids
    g.vs['neuron_name'] = g.vs['label'] = neuron_names
    g.es['weight'] = weight

    return g


def neuron2nx(x):
    """ Turn CatmaidNeuron into an NetworkX DiGraph.

    Parameters
    ----------
    x :         {CatmaidNeuron, CatmaidNeuronList}

    Returns
    -------
    networkx.DiGraph
                NetworkX representation of the neuron. Returns list of graphs
                if x is multiple neurons. 

    """ 

    if isinstance(x, ( pd.DataFrame, core.CatmaidNeuronList)):
        return [neuron2nx(x.loc[i]) for i in range(x.shape[0])]
    elif isinstance(x, (pd.Series, core.CatmaidNeuron)):
        pass
    else:
        raise ValueError('Unable input type "{0}"'.format(type(x)))

    # Collect nodes
    nodes = x.nodes.set_index('treenode_id')
    # Collect edges
    edges = x.nodes[~x.nodes.parent_id.isnull()][['treenode_id','parent_id']].values
    # Collect weight
    weights = np.sqrt( np.sum( (nodes.loc[ edges[:,0], ['x','y','z'] ].values.astype(int) 
                                - nodes.loc[edges[:,1], ['x','y','z'] ].values.astype(int) )**2, axis=1) ) 
    # Generate weight dictionary
    edge_dict = np.array( [ { 'weight' : w } for w in weights ] )
    # Add weights to dictionary
    edges = np.append( edges, edge_dict.reshape( len( edges ), 1 ), axis=1)
    # Create empty directed Graph
    g = nx.DiGraph()    
    # Add nodes (in case we have disconnected nodes)
    g.add_nodes_from( x.nodes.treenode_id.values )
    # Add edges
    g.add_edges_from( edges )

    return g


def neuron2igraph(x):
    """ Turns CatmaidNeuron(s) into an iGraph graph. Requires iGraph to be
    installed.

    Parameters
    ----------
    x :         {CatmaidNeuron, CatmaidNeuronList}

    Returns
    -------
    igraph.Graph(directed=True)
                Representation of the neuron. Returns list of graphs
                if x is multiple neurons.

    """
    # If iGraph is not installed return nothing
    if igraph == None:
        return None

    if isinstance(x, ( pd.DataFrame, core.CatmaidNeuronList)):
        return [neuron2igraph(x.loc[i]) for i in range(x.shape[0])]
    elif isinstance(x, (pd.Series, core.CatmaidNeuron)):
        pass
    else:
        raise ValueError('Unable input type "{0}"'.format(type(x)))

    module_logger.debug('Generating graph from skeleton data...')

    # Make sure we have correctly numbered indices
    nodes = x.nodes.reset_index(drop=True)

    # Generate list of vertices -> this order is retained
    vlist = nodes.treenode_id.tolist()

    # Get list of edges as indices (needs to exclude root node)
    tn_index_with_parent = nodes[
        ~nodes.parent_id.isnull()].index.values
    parent_ids = nodes[~nodes.parent_id.isnull()].parent_id.values
    nodes['temp_index'] = nodes.index  # add temporary index column
    parent_index = nodes.set_index('treenode_id').loc[parent_ids,
        'temp_index'].values    

    # Generate list of edges based on index of vertices
    elist = list(zip(tn_index_with_parent, parent_index))

    # Generate graph and assign custom properties
    g = igraph.Graph(elist, n=len(vlist), directed=True)

    g.vs['node_id'] = nodes.treenode_id.tolist()
    g.vs['parent_id'] = nodes.parent_id.tolist()   

    # Generate weights by calculating edge lengths = distance between nodes
    tn_coords = nodes.loc[[e[0] for e in elist], ['x', 'y', 'z']].values
    parent_coords = nodes.loc[[e[1] for e in elist], ['x', 'y', 'z']].values

    w = np.sqrt(np.sum((tn_coords - parent_coords) ** 2, axis=1).astype(float)).tolist()
    g.es['weight'] = w

    return g


def dist_from_root(data, synapses_only=False):
    """ Get geodesic distance to root in nano meters (nm) for all treenodes. 

    Parameters
    ----------
    data :            {graph object, pandas.DataFrame, CatmaidNeuron}
                      Holds the skeleton data.
    synapses_only :   bool, optional
                      If True, only distances for nodes with synapses will be 
                      returned (only makes sense if input is a Graph).

    Returns
    -------     
    dict             
                      Only if ``data`` is a graph object. 
                      Format ``{node_id : distance_to_root }``

    pandas DataFrame 
                      Only if ``data`` is a pandas DataFrame:. With 
                      ``df.nodes.dist_to_root`` holding the distances to root. 

    """

    if isinstance(data, igraph.Graph):
        g = data
    elif isinstance(data, ( pd.DataFrame, core.CatmaidNeuronList)):
        return [dist_from_root(data.loc[i]) for i in range(data.shape[0])]
    elif isinstance(data, (pd.Series, core.CatmaidNeuron)):
        g = data.igraph
        if g is None:
            g = neuron2igraph(data)
    else:
        raise Exception('Unexpected data type "{0}"'.format(type(data)))

    # Generate distance matrix.
    try:
        module_logger.info('Generating distance matrix for neuron %s #%s...' % (
            data.neuron_name, str(data.skeleton_id)))
    except:
        module_logger.info('Generating distance matrix for igraph...')

    distance_matrix = g.shortest_paths_dijkstra(mode='All', weights='weight')

    if synapses_only:
        nodes = [ (v.index, v['node_id']) 
                 for v in g.vs.select(_node_id_in=data.connectors.treenode_id )]
    else:
        nodes = [(v.index, v['node_id']) for v in g.vs]

    root = [v.index for v in g.vs if v['parent_id'] == None][0]

    distances_to_root = {}

    for n in nodes:
        distances_to_root[n[1]] = distance_matrix[n[0]][root]

    if isinstance(data, igraph.Graph):
        return distances_to_root
    else:
        data.nodes['dist_to_root'] = [distances_to_root[n]
                                      for n in data.nodes.treenode_id.tolist()]
        data.igraph = neuron2igraph(data)
        return data


def _find_all_paths(g, start, end, mode = 'OUT', maxlen = None):
    """ Find all paths between two vertices in an iGraph object. For some reason
    this function exists in R iGraph but not Python iGraph. This is rather slow
    and should not be used for large graphs.
    """

    def find_all_paths_aux(adjlist, start, end, path, maxlen = None):
        path = path + [start]
        if start == end:
            return [path]
        paths = []
        if maxlen is None or len(path) <= maxlen:
            for node in adjlist[start] - set(path):
                paths.extend(find_all_paths_aux(adjlist, node, end, path, maxlen))
        return paths

    adjlist = [set(g.neighbors(node, mode = mode)) \
        for node in range(g.vcount())]
    all_paths = []
    start = start if type(start) is list else [start]
    end = end if type(end) is list else [end]
    for s in start:
        for e in end:
            all_paths.extend(find_all_paths_aux(adjlist, s, e, [], maxlen))
    return all_paths