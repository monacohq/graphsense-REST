import cassandra.cluster
from cassandra.query import named_tuple_factory, dict_factory
from flask import abort
import graphsensemodel as gm


session = None
tx_query = {}
txs_query = {}
block_query = {}
block_transactions_query = {}
blocks_query = {}
exchange_rates_query = {}
exchange_rate_for_height_query = {}
address_query = {}
address_transactions_query = {}
address_transactions_without_limit_query = {}
address_tags_query = {}
address_search_query = {}
label_search_query = None
label_query = None
transaction_search_query = {}
address_cluster_query = {}
cluster_tags_query = {}
cluster_query = {}
address_incoming_relations_query = {}
address_incoming_relations_without_limit_query = {}
address_outgoing_relations_query = {}
address_outgoing_relations_without_limit_query = {}
cluster_incoming_relations_query = {}
cluster_incoming_relations_without_limit_query = {}
cluster_outgoing_relations_query = {}
cluster_outgoing_relations_without_limit_query = {}
cluster_addresses_query = {}
cluster_addresses_without_limit_query = {}
block_height_query = {}
statistics_query = {}
keyspace_mapping = {}
all_exchange_rates = {}
last_height = {}


def query_exchange_rates(currency, offset, limit):
    if not offset:
        offset = 0
    if not limit:
        limit = 100
    start = last_height[currency] - limit*offset
    end = last_height[currency] - limit*(offset+1)
    exchange_rates = [gm.ExchangeRate(all_exchange_rates[currency][height]).__dict__
                      for height in range(start, end, -1)]
    return exchange_rates


def query_block(currency, height):
    set_keyspace(session, currency, space="raw")
    if height > last_height[currency]:
        abort(404, "Block not available yet")
    result = session.execute(block_query[currency], [height])
    return gm.Block(result[0]).__dict__ if result else None


def query_statistics(currency):
    set_keyspace(session, currency)
    result = session.execute(statistics_query[currency])
    return gm.Statistics(result[0]).__dict__ if result else None


def query_block_transactions(currency, height):
    set_keyspace(session, currency, space="raw")
    if height > last_height[currency]:
        abort(404, "Block not available yet")
    result = session.execute(block_transactions_query[currency], [height])
    return gm.BlockWithTransactions(result[0], query_exchange_rate_for_height(currency, height)).__dict__ if result else None


def query_blocks(currency, page_state):
    set_keyspace(session, currency, space="raw")
    if page_state:
        results = session.execute(blocks_query[currency], paging_state=page_state)
    else:
        results = session.execute(blocks_query[currency], [10])
    page_state = results.paging_state
    blocks = [gm.Block(row).__dict__ for row in results]
    return page_state, blocks


def query_transaction(currency, txHash):
    set_keyspace(session, currency, space="raw")
    try:
        rows = session.execute(tx_query[currency], [txHash[0:5], bytearray.fromhex(txHash)])
    except Exception:
        abort(404, "Transaction hash is not hex")
    return gm.Transaction(rows[0], query_exchange_rate_for_height(currency, rows[0].height)).__dict__ if rows else None


def query_transactions(currency, page_state):
    set_keyspace(session, currency, space="raw")
    if page_state:
        results = session.execute(txs_query[currency], paging_state=page_state)
    else:
        results = session.execute(txs_query[currency], [10])

    page_state = results.paging_state
    transactions = [gm.Transaction(row, query_exchange_rate_for_height(currency, row.height)).__dict__
                    for row in results]
    return page_state, transactions


def query_transaction_search(currency, expression):
    set_keyspace(session, currency, space="raw")
    transactions = session.execute(transaction_search_query[currency],
                                   [expression])
    transactions._fetch_all()
    return transactions


def query_address_search(currency, expression):
    set_keyspace(session, currency)
    addresses = session.execute(address_search_query[currency], [expression])
    addresses._fetch_all()
    return addresses


def query_label_search(expression_norm_prefix):
    set_keyspace(session, "", space="tagpacks")
    labels = session.execute(label_search_query, [expression_norm_prefix])
    labels._fetch_all()
    return labels


def query_tags(label_norm_prefix, label_norm):
    set_keyspace(session, "", space="tagpacks")
    labels = session.execute(tags_query, [label_norm_prefix, label_norm])
    labels._fetch_all()
    def makeTagWithCurrency(row):
        d = gm.Tag(row).__dict__
        d["currency"] = row.currency
        return d

    tags = [makeTagWithCurrency(row) for row in labels]
    return tags

def query_label(label_norm_prefix, label_norm):
    set_keyspace(session, "", space="tagpacks")
    label = session.execute(label_query, [label_norm_prefix, label_norm])
    return gm.Label(label[0]).__dict__ if label else None

def query_address(currency, address):
    set_keyspace(session, currency)
    rows = session.execute(address_query[currency], [address, address[0:5]])
    return gm.Address(rows[0], gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])) if rows else None


def query_address_cluster(currency, address):
    set_keyspace(session, currency)
    clusterid = query_address_cluster_id(currency, address)
    ret = {}
    if clusterid:
        cluster_obj = query_cluster(currency, clusterid)
        ret = cluster_obj.__dict__
    return ret

def query_address_cluster_id(currency, address):
    set_keyspace(session, currency)
    clusterids = session.execute(address_cluster_query[currency],
                                 [address, address[0:5]])
    if clusterids:
        return clusterids[0].cluster
    return None

def query_address_transactions(currency, page_state, address, pagesize, limit):
    set_keyspace(session, currency)

    if limit is None:
        query = address_transactions_without_limit_query
        params = [address, address[0:5]]
    else:
        query = address_transactions_query
        params = [address, address[0:5], limit]

    if pagesize:
        query[currency].fetch_size = pagesize

    if page_state:
        rows = session.execute(query[currency], params, paging_state=page_state)
    else:
        rows = session.execute(query[currency], params)

    page_state = rows.paging_state
    return page_state, [row for row in rows.current_rows]


def query_address_tags(currency, address):
    set_keyspace(session, currency)
    tags = session.execute(address_tags_query[currency], [address])
    return [gm.Tag(row).__dict__ for row in tags]

def query_address_with_tags(currency, address):
    result = query_address(currency, address)
    result.tags = query_address_tags(currency, address)
    return result

def query_implicit_tags(currency, address):
    set_keyspace(session, currency)
    clusters = session.execute(address_cluster_query[currency], [address, address[0:5]])
    implicit_tags = []
    for (clusterrow) in clusters:
        clustertags = query_cluster_tags(currency, clusterrow.cluster)
        if clustertags:
            implicit_tags.extend(clustertags)
    return implicit_tags


def query_address_incoming_relations(currency, page_state, address, pagesize, limit):
    set_keyspace(session, currency)
    if limit is None:
        query = address_incoming_relations_without_limit_query
        params = [address[0:5], address]
    else:
        query = address_incoming_relations_query
        params = [address[0:5], address, limit]

    if pagesize:
        query[currency].fetch_size = pagesize

    if page_state:
        rows = session.execute(query[currency], params, paging_state=page_state)
    else:
        rows = session.execute(query[currency], params)

    page_state = rows.paging_state
    exchange_rate = gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])
    relations = [gm.AddressIncomingRelations(row, exchange_rate)
                 for row in rows.current_rows]
    return page_state, relations


def query_address_outgoing_relations(currency, page_state, address, pagesize, limit):
    set_keyspace(session, currency)
    if limit is None:
        query = address_outgoing_relations_without_limit_query
        params = [address[0:5], address]
    else:
        query = address_outgoing_relations_query
        params = [address[0:5], address, limit]

    if pagesize is not None:
        query[currency].fetch_size = pagesize

    if page_state is not None:
        rows = session.execute(query[currency], params, paging_state=page_state)
    else:
        rows = session.execute(query[currency], params)

    page_state = rows.paging_state
    exchange_rate = gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])
    relations = [gm.AddressOutgoingRelations(row, exchange_rate)
                 for row in rows.current_rows]
    return page_state, relations


def query_cluster(currency, cluster):
    set_keyspace(session, currency)
    rows = session.execute(cluster_query[currency], [int(cluster)])
    return gm.Cluster(rows.current_rows[0],
                      gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])) if rows else None


def query_cluster_tags(currency, cluster):
    set_keyspace(session, currency)
    tags = session.execute(cluster_tags_query[currency], [int(cluster)])
    clustertags = [gm.Tag(tagrow).__dict__ for (tagrow) in tags]
    return clustertags


def query_cluster_addresses(currency, cluster, page, pagesize, limit):
    set_keyspace(session, currency)
    if limit is None:
        query = cluster_addresses_without_limit_query
        params = [int(cluster)]
    else:
        query = cluster_addresses_query
        params = [int(cluster), limit]

    if pagesize is not None:
        query[currency].fetch_size = pagesize

    if page:
        rows = session.execute(query[currency], params, paging_state=page)
    else:
        rows = session.execute(query[currency], params)

    clusteraddresses = [gm.ClusterAddresses(row, gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])).__dict__
                        for row in rows.current_rows]
    page = rows.paging_state
    return page, clusteraddresses


def query_cluster_incoming_relations(currency, page_state, cluster, pagesize, limit):
    set_keyspace(session, currency)
    if limit is None:
        query = cluster_incoming_relations_without_limit_query
        params = [cluster]
    else:
        query = cluster_incoming_relations_query
        params = [cluster, limit]

    if pagesize:
        query[currency].fetch_size = pagesize

    if page_state:
        rows = session.execute(query[currency], params, paging_state=page_state)
    else:
        rows = session.execute(query[currency], params)

    page_state = rows.paging_state
    exchange_rate = gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])
    relations = [gm.ClusterIncomingRelations(row, exchange_rate) for row in rows.current_rows]
    return page_state, relations


def query_cluster_outgoing_relations(currency, page_state, cluster, pagesize, limit):
    set_keyspace(session, currency)
    if limit is None:
        query = cluster_outgoing_relations_without_limit_query
        params = [cluster]
    else:
        query = cluster_outgoing_relations_query
        params = [cluster, limit]

    if pagesize:
        query[currency].fetch_size = pagesize

    if page_state:
        rows = session.execute(query[currency], params, paging_state=page_state)
    else:
        rows = session.execute(query[currency], params)
    page_state = rows.paging_state
    exchange_rate = gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])
    relations = [gm.ClusterOutgoingRelations(row, exchange_rate) for row in rows.current_rows]
    return page_state, relations

def query_cluster_search_neighbors(currency, cluster, isOutgoing, category, ids, breadth, depth):
    set_keyspace(session, currency)
    if depth <= 0:
        return []


    if isOutgoing:
        (_, rows) = query_cluster_outgoing_relations(currency, None, cluster, breadth, breadth)
    else:
        (_, rows) = query_cluster_incoming_relations(currency, None, cluster, breadth, breadth)

    paths = []

    for row in rows:
        subcluster = row.dstCluster if isOutgoing else row.srcCluster
        if not subcluster.isdigit():
            continue
        match = True
        tags = query_cluster_tags(currency, subcluster)

        if category != None:
            # find first occurence of category in tags
            match = next((True for t in tags if t["category"] == category), False)

        matchingAddresses = []
        if match and ids != None:
            matchingAddresses = [id["address"] for id in ids if str(id["cluster"]) == str(subcluster)]
            match = len(matchingAddresses) > 0

        if match:
            subpaths = True
        else:
            subpaths = query_cluster_search_neighbors(currency, subcluster, isOutgoing, category, ids, breadth, depth - 1)

        if not subpaths:
            continue
        props = query_cluster(currency, subcluster).__dict__
        props["tags"] = tags
        obj = {"node": props, "relation": row.toJson(), "matchingAddresses": []}
        if subpaths == True:
            addresses_with_tags = [ query_address_with_tags(currency, address) for address in matchingAddresses ]
            obj["matchingAddresses"] = [ address for address in addresses_with_tags if address is not None ]
            subpaths = None
        obj["paths"] = subpaths
        paths.append(obj)
    return paths

def set_keyspace(session, currency=None, space="transformed"):
    if space == "tagpacks":
        session.set_keyspace(keyspace_mapping["tagpacks"])
        return

    if currency in keyspace_mapping:
        if space == "raw":
            session.set_keyspace(keyspace_mapping[currency][0])
        elif space == "transformed":
            session.set_keyspace(keyspace_mapping[currency][1])
        else:
            abort(404, "Keyspace %s not allowed" % space)
    else:
        abort(404, "Currency %s does not exist" % currency)


def query_all_exchange_rates(currency, h_max):
    try:
        set_keyspace(session, currency, space="raw")
        session.row_factory = dict_factory
        session.default_fetch_size = None
        print("Loading exchange rates for %s ..." % currency)
        results = session.execute(exchange_rates_query[currency], [h_max],
                                  timeout=180)
        d = {row["height"]: {"eur": row["eur"], "usd": row["usd"]}
             for row in results}
        print("Rates loaded.")
        session.row_factory = named_tuple_factory  # reset default
        return d
    except Exception as e:
        session.row_factory = named_tuple_factory
        print("Failed to query exchange rates. Cause: \n%s" % str(e))
        raise SystemExit


def query_last_block_height(currency):
    set_keyspace(session, currency, space="raw")
    block_max = 0
    block_inc = 100000
    while True:
        rs = session.execute(block_height_query[currency], [block_max])
        if not rs:
            if block_max == 0:
                return 0
            if block_inc == 1:
                return block_max - 1
            else:
                block_max -= block_inc
                block_inc //= 10
        else:
            block_max += block_inc


def query_exchange_rate_for_height(currency, height):
    if height <= last_height[currency]:
        res = gm.ExchangeRate(all_exchange_rates[currency][height])
    else:
        res = gm.ExchangeRate(all_exchange_rates[currency][last_height[currency]])
    return res


def connect(app):
    global address_cluster_query, address_incoming_relations_query, \
           address_outgoing_relations_query, address_query, \
           address_search_query, address_tags_query, \
           address_transactions_query, all_exchange_rates, \
           block_height_query, block_query, block_transactions_query, \
           blocks_query, cluster_addresses_query, \
           cluster_incoming_relations_query, \
           cluster_outgoing_relations_query, \
           cluster_query, cluster_tags_query, keyspace_mapping, \
           exchange_rate_for_height_query, exchange_rates_query, \
           last_height, session, statistics_query, transaction_search_query, \
           tx_query, txs_query, label_search_query, label_query, tags_query

    cluster = cassandra.cluster.Cluster(app.config["CASSANDRA_NODES"])
    app.logger.debug("Created new Cassandra cluster.")

    # set the first keyspace in mapping to the default in order to be able to
    # create the prepared statements; alternative strategy is to not use
    # prepared statements and specify the keyspace in the query string
    keyspace_mapping = app.config["MAPPING"]
    if "tagpacks" in keyspace_mapping.keys() and keyspace_mapping["tagpacks"] == "tagpacks":
        keyspace_name = "tagpacks"  # it must be "tagpacks"
    else:
        abort(404, "Tagpacks keyspace missing")

    session = cluster.connect(keyspace_mapping[keyspace_name])
    session.default_fetch_size = 10
    app.logger.debug("Created new Cassandra session.")
    label_search_query = session.prepare("SELECT label,label_norm FROM tag_by_label WHERE label_norm_prefix = ? GROUP BY label_norm_prefix, label_norm")
    label_query = session.prepare("SELECT label_norm, label_norm_prefix, label, COUNT(address) as address_count FROM tag_by_label WHERE label_norm_prefix = ? and label_norm = ? GROUP BY label_norm_prefix, label_norm")
    tags_query = session.prepare("SELECT * FROM tag_by_label WHERE label_norm_prefix = ? and label_norm = ?")
    for keyspace_name in keyspace_mapping.keys():
        if keyspace_name == "tagpacks":
            continue
        set_keyspace(session, keyspace_name)
        address_query[keyspace_name] = session.prepare("SELECT * FROM address WHERE address = ? AND address_prefix = ?")
        address_search_query[keyspace_name] = session.prepare("SELECT address FROM address WHERE address_prefix = ?")
        address_transactions_query[keyspace_name] = session.prepare("SELECT * FROM address_transactions WHERE address = ? AND address_prefix = ? LIMIT ?")
        address_transactions_without_limit_query[keyspace_name] = session.prepare("SELECT * FROM address_transactions WHERE address = ? AND address_prefix = ?")
        address_tags_query[keyspace_name] = session.prepare("SELECT * FROM address_tags WHERE address = ?")
        address_cluster_query[keyspace_name] = session.prepare("SELECT cluster FROM address_cluster WHERE address = ? AND address_prefix = ?")
        address_incoming_relations_query[keyspace_name] = session.prepare("SELECT * FROM address_incoming_relations WHERE dst_address_prefix = ? AND dst_address = ? LIMIT ?")
        address_incoming_relations_without_limit_query[keyspace_name] = session.prepare("SELECT * FROM address_incoming_relations WHERE dst_address_prefix = ? AND dst_address = ?")
        address_outgoing_relations_query[keyspace_name] = session.prepare("SELECT * FROM address_outgoing_relations WHERE src_address_prefix = ? AND src_address = ? LIMIT ?")
        address_outgoing_relations_without_limit_query[keyspace_name] = session.prepare("SELECT * FROM address_outgoing_relations WHERE src_address_prefix = ? AND src_address = ?")
        cluster_incoming_relations_query[keyspace_name] = session.prepare("SELECT * FROM cluster_incoming_relations WHERE dst_cluster = ? LIMIT ?")
        cluster_incoming_relations_without_limit_query[keyspace_name] = session.prepare("SELECT * FROM cluster_incoming_relations WHERE dst_cluster = ?")
        cluster_outgoing_relations_query[keyspace_name] = session.prepare("SELECT * FROM cluster_outgoing_relations WHERE src_cluster = ? LIMIT ?")
        cluster_outgoing_relations_without_limit_query[keyspace_name] = session.prepare("SELECT * FROM cluster_outgoing_relations WHERE src_cluster = ?")
        cluster_tags_query[keyspace_name] = session.prepare("SELECT * FROM cluster_tags WHERE cluster = ?")
        cluster_query[keyspace_name] = session.prepare("SELECT * FROM cluster WHERE cluster = ?")
        cluster_addresses_query[keyspace_name] = session.prepare("SELECT * FROM cluster_addresses WHERE cluster = ? LIMIT ?")
        cluster_addresses_without_limit_query[keyspace_name] = session.prepare("SELECT * FROM cluster_addresses WHERE cluster = ?")
        statistics_query[keyspace_name] = session.prepare("SELECT * FROM summary_statistics LIMIT 1")

        set_keyspace(session, keyspace_name, space="raw")
        tx_query[keyspace_name] = session.prepare("SELECT * FROM transaction WHERE tx_prefix = ? AND tx_hash = ?")
        txs_query[keyspace_name] = session.prepare("SELECT * FROM transaction LIMIT ?")
        transaction_search_query[keyspace_name] = session.prepare("SELECT tx_hash from transaction where tx_prefix = ?")
        block_transactions_query[keyspace_name] = session.prepare("SELECT * FROM block_transactions WHERE height = ?")
        block_query[keyspace_name] = session.prepare("SELECT * FROM block WHERE height = ?")
        blocks_query[keyspace_name] = session.prepare("SELECT * FROM block LIMIT ?")
        exchange_rates_query[keyspace_name] = session.prepare("SELECT * FROM exchange_rates LIMIT ?")
        exchange_rate_for_height_query[keyspace_name] = session.prepare("SELECT * FROM exchange_rates WHERE height = ?")
        block_height_query[keyspace_name] = session.prepare("SELECT height FROM exchange_rates WHERE height = ?")

        last_height[keyspace_name] = query_last_block_height(keyspace_name)
        all_exchange_rates[keyspace_name] = query_all_exchange_rates(keyspace_name,
                                                                last_height[keyspace_name])

    app.logger.debug("Created prepared statements")
