from flask import Flask, jsonify, request, abort
from flask_restplus import Api, Resource, fields
from flask_cors import CORS
import graphsensedao as gd
import graphsensemodel as gm
import json

with open("./config.json", "r") as fp:
    config = json.load(fp)

app = Flask(__name__)
api = Api(app=app, version='0.4', description='REST Interface for Graphsense')

limit_parser = api.parser()
limit_parser.add_argument('limit', type=int, location='args')

limit_offset_parser = limit_parser.copy()
limit_offset_parser .add_argument('offset', type=int, location='args')

limit_query_parser = limit_parser.copy()
limit_query_parser.add_argument('q', location='args')

limit_direction_parser = limit_parser.copy()
limit_direction_parser .add_argument('direction', location='args')

page_parser = api.parser()
page_parser.add_argument('page', location='args')  # TODO: find right type

CORS(app)
app.config.from_object(__name__)
app.config.update(config)
app.config.from_envvar("GRAPHSENSE_REST_SETTINGS", silent=True)
currency_mapping = app.config["MAPPING"]

value_response = api.model('value_response  ', {
    'eur': fields.Integer(required=True, description='Euro value'),
    'satoshi': fields.Integer(required=True, description='Satoshi value'),
    'usd': fields.Integer(required=True, description='USD value')
})

@api.route("/")
class Statistics(Resource):
    def get(self):
        """
        Returns a JSON with statistics of all the available currencies
        """
        statistics = dict()
        for currency in currency_mapping.keys():
            if len(currency.split("_")) == 1:
                statistics[currency] = gd.query_statistics(currency)
        return statistics


@api.route("/<currency>/exchangerates")
class ExchangeRates(Resource):
    @api.doc(parser=limit_offset_parser)
    def get(self, currency):
        """
        Returns a JSON with exchange rates
        """
        manual_limit = 100000
        limit = request.args.get("limit")
        offset = request.args.get("offset")
        if offset and not isinstance(offset, int):
            abort(404, "Invalid offset")
        if limit and (not isinstance(offset, int) or limit > manual_limit):
            abort(404, "Invalid limit")

        exchange_rates = gd.query_exchange_rates(currency, offset, limit)
        return jsonify({
            "exchangeRates": exchange_rates
        })


block_response = api.model('block_response', {
    "blockHash": fields.String(required=True, description='Block hash'),
    "height": fields.Integer(required=True, description='Block height'),
    "noTransactions": fields.Integer(required=True, description='Number of transactions'),
    'timestamp': fields.Integer(required=True, description='Transaction timestamp'),
})

@api.route("/<currency>/block/<int:height>")
class Block(Resource):
    @api.marshal_with(block_response)
    def get(self, currency, height):
        """
        Returns a JSON with minimal block details
        """
        block = gd.query_block(currency, height)
        if not block:
            abort(404, "Block height %d not found" % height)
        return jsonify(block)


blocks_response = api.model('blocks_response', {
    "Blocks": fields.List(fields.Nested(block_response), required=True, description='Block list'),
    'nextPage': fields.String(required=True, description='The next page')
})

@api.route("/<currency>/blocks")
class Blocks(Resource):
    @api.doc(parser=page_parser)
    @api.marshal_with(blocks_response)
    def get(self, currency):
        """
        Returns a JSON with 10 blocks per page
        """
        page_state = request.args.get("page")
        (page_state, blocks) = gd.query_blocks(currency, page_state)
        return {"nextPage": page_state.hex() if page_state is not None else None, "blocks": blocks}


block_transaction_response = api.model('block_transaction_response', {
    "noInputs": fields.Integer(required=True, description='Number of inputs'),
    "noOutputs": fields.Integer(required=True, description='Number of outputs'),
    'totalInput': fields.Nested(value_response, required=True, description='Total input value'),
    'totalOutput': fields.Nested(value_response, required=True, description='Total output value'),
    'txHash': fields.String(required=True, description='Transaction hash')
})

block_transactions_response = api.model('block_transactions_response', {
    "height": fields.Integer(required=True, description='Block height'),
    "txs": fields.List(fields.Nested(block_transaction_response), required=True, description='Block list')
})

@api.route("/<currency>/block/<int:height>/transactions")
class BlockTransactions(Resource):
    @api.marshal_with(block_transactions_response)
    def get(self, currency, height):
        """
        Returns a JSON with all the transactions of the block
        """
        block_transactions = gd.query_block_transactions(currency, height)
        if not block_transactions:
            abort(404, "Block height %d not found" % height)
        return jsonify(block_transactions)


input_output_response = api.model('input_output_response', {
    'address': fields.String(required=True, description='Address'),
    'value': fields.Nested(value_response, required=True, description='Ionput/Output value')
})

transaction_response = api.model('transaction_response', {
    'txHash': fields.String(required=True, description='Transaction hash'),
    'coinbase': fields.Boolean(required=True, description='Coinbase transaction flag'),
    'height': fields.Integer(required=True, description='Transaction height'),
    'inputs': fields.List(fields.Nested(input_output_response), required=True, description='Transaction inputs'),
    'outputs': fields.List(fields.Nested(input_output_response), required=True, description='Transaction inputs'),
    'timestamp': fields.Integer(required=True, description='Transaction timestamp'),
    'totalInput': fields.Nested(value_response, required=True),
    'totalOutput': fields.Nested(value_response, required=True),
})

@api.route("/<currency>/tx/<txHash>")
class Transaction(Resource):
    @api.marshal_with(transaction_response)
    def get(self, currency, txHash):
        """
        Returns a JSON with the details of the transaction
        """
        transaction = gd.query_transaction(currency, txHash)
        if not transaction:
            abort(404, "Transaction id %s not found" % txHash)
        return transaction


transactions_response = api.model('transactions_response', {
    'nextPage': fields.String(required=True, description='The next page'),
    'transactions': fields.List(fields.Nested(transaction_response), required=True, description='The list of transactions')
})

@api.route("/<currency>/transactions")
class Transactions(Resource):
    @api.doc(parser=page_parser)
    @api.marshal_with(transactions_response)
    def get(self, currency):
        """
        Returns a JSON with the details of 10 transactions per page
        """
        page_state = request.args.get("page")
        (page_state, transactions) = gd.query_transactions(currency, page_state)
        return {
            "nextPage": page_state.hex() if page_state is not None else None,
            "transactions": transactions
        }


search_response = api.model('search_response', {
    'addresses': fields.List(fields.String, required=True, description='The list of found addresses'),
    'transactions': fields.List(fields.String, required=True, description='The list of found transactions')
})

@api.route("/<currency>/search")
class Search(Resource):
    @api.doc(parser=limit_query_parser)
    @api.marshal_with(search_response)
    def get(self, currency):
        """
        Returns a JSON with a list of matching addresses and a list of matching transactions
        """
        expression = request.args.get("q")
        if not expression:
            abort(404, "Expression parameter not provided")
        leading_zeros = 0
        pos = 0
        # leading zeros will be lost when casting to int
        while expression[pos] == "0":
            pos += 1
            leading_zeros += 1
        limit = request.args.get("limit")
        if not limit:
            limit = 50
        else:
            try:
                limit = int(limit)
            except Exception:
                abort(404, "Invalid limit value")
        if len(expression) >= 5:
            prefix = expression[:5]
        else:
            # returns an empty list because the user did not input enough chars
            prefix = expression
        # no limit here, else we miss the specified transaction
        transactions = gd.query_transaction_search(currency, prefix)
        # no limit here, else we miss the specified address
        addresses = gd.query_address_search(currency, prefix)

        return {
            "addresses": [row.address for row in addresses.current_rows
                          if row.address.startswith(expression)][:limit],
            "transactions": [tx for tx in ["0"*leading_zeros +
                                           str(hex(int.from_bytes(row.tx_hash, byteorder="big")))[2:]
                                           for row in transactions.current_rows]
                             if tx.startswith(expression)][:limit]
        }


tx_response = api.model('tx_response', {
    'height': fields.Integer(required=True, description='Transaction height'),
    'timestamp': fields.Integer(required=True, description='Transaction timestamp'),
    'tx_hash': fields.String(required=True, description='Transaction hash')
})


address_response = api.model('address_response', {
    'address': fields.String(required=True, description='Address'),
    'address_prefix': fields.String(required=True, description='Address prefix'),
    'balance': fields.Nested(value_response, required=True),
    'firstTx': fields.Nested(tx_response, required=True),
    'lastTx': fields.Nested(tx_response, required=True),
    'inDegree': fields.Integer(required=True, description='inDegree value'),
    'outDegree': fields.Integer(required=True, description='outDegree value'),
    'noIncomingTxs': fields.Integer(required=True, description='Incomming transactions'),
    'noOutgoingTxs': fields.Integer(required=True, description='Outgoing transactions'),
    'totalReceived': fields.Nested(value_response, required=True),
    'totalSpent': fields.Nested(value_response, required=True)
})

@api.route("/<currency>/address/<address>")
class Address(Resource):
    @api.marshal_with(address_response)
    def get(self, currency, address):
        """
        Returns a JSON with the details of the address
        """
        if not address:
            abort(404, "Address not provided")

        result = gd.query_address(currency, address)
        if result:
            return result.__dict__
        else:
            abort(404, "Address not found")
        #return jsonify(result.__dict__) if result else jsonify({})

tag_response = api.model('address_tag_response', {
    'actorCategory': fields.String(required=True, description='Actor category'),
    'address': fields.String(required=True, description='Address'),
    'description': fields.String(required=True, description='Description'),
    'source': fields.String(required=True, description='Source'),
    'sourceUri': fields.String(required=True, description='Source URI'),
    'tag': fields.String(required=True, description='Tag'),
    'tagUri': fields.String(required=True, description='Tag URI'),
    'timestamp': fields.Integer(required=True, description='Transaction timestamp')
})

@api.route("/<currency>/address/<address>/tags")
class AddressTags(Resource):
    @api.marshal_list_with(tag_response)
    def get(self, currency, address):
        """
        Returns a JSON with the explicit tags of the address
        """
        if not address:
            abort(404, "Address not provided")

        tags = gd.query_address_tags(currency, address)
        return tags

address_with_tags_response = api.model('address_with_tags_response', {
    'address': fields.String(required=True, description='Address'),
    'address_prefix': fields.String(required=True, description='Address prefix'),
    'balance': fields.Nested(value_response, required=True),
    'firstTx': fields.Nested(tx_response, required=True),
    'lastTx': fields.Nested(tx_response, required=True),
    'inDegree': fields.Integer(required=True, description='inDegree value'),
    'outDegree': fields.Integer(required=True, description='outDegree value'),
    'noIncomingTxs': fields.Integer(required=True, description='Incomming transactions'),
    'noOutgoingTxs': fields.Integer(required=True, description='Outgoing transactions'),
    'totalReceived': fields.Nested(value_response, required=True),
    'totalSpent': fields.Nested(value_response, required=True),
    'tags': fields.List(fields.Nested(tag_response, required=True))
})

@api.route("/<currency>/address_with_tags/<address>")
class AddressWithTags(Resource):
    @api.marshal_with(address_with_tags_response)
    def get(self, currency, address):
        """
        Returns a JSON with the transactions of the address
        """
        if not address:
            abort(404, "Address not provided")

        result = gd.query_address(currency, address)
        result.tags = gd.query_address_tags(currency, address)
        return jsonify(result.__dict__) if result else jsonify({})


address_transaction_response = api.model('address_transaction_response', {
    'address': fields.String(required=True, description='Address'),
    'address_prefix': fields.String(required=True, description='Address prefix'),
    'height': fields.Integer(required=True, description='Transaction height'),
    'timestamp': fields.Integer(required=True, description='Transaction timestamp'),
    'txHash': fields.String(required=True, description='Transaction hash'),
    'txIndex': fields.Integer(required=True, description='Transaction index'),
    'value': fields.Nested(value_response, required=True)
})

address_transactions_response = api.model('address_transactions_response', {
    'nextPage': fields.String(required=True, description='The next page'),
    'transactions': fields.List(fields.Nested(address_transaction_response), required=True, description='The list of transactions')
})

@api.route("/<currency>/address/<address>/transactions")
class AddressTransactions(Resource):
    @api.doc(parser=limit_parser)
    @api.marshal_with(address_transactions_response)
    def get(self, currency, address):
        """
        Returns a JSON with the transactions of the address
        """
        if not address:
            abort(404, "Address not provided")
        limit = request.args.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except Exception:
                abort(404, "Invalid limit value")

        pagesize = request.args.get("pagesize")
        if pagesize is not None:
            try:
                pagesize = int(pagesize)
            except Exception:
                abort(404, "Invalid pagesize value")

        page_state = request.args.get("page")
        (page_state, rows) = gd.query_address_transactions(
            currency, page_state, address, pagesize, limit)
        txs = [gm.AddressTransactions(
                   row, gd.query_exchange_rate_for_height(currency, row.height)
               ).__dict__
               for row in rows]
        return {
            "nextPage": page_state.hex() if page_state is not None else None,
            "transactions": txs
        }


@api.route("/<currency>/address/<address>/implicitTags")
class AddressImplicitTags(Resource):
    @api.marshal_list_with(tag_response)
    def get(self, currency, address):
        """
        Returns a JSON with the implicit tags of the address
        """
        if not address:
            abort(404, "Address not provided")

        implicit_tags = gd.query_implicit_tags(currency, address)
        return implicit_tags


cluster_response = api.model('address_cluster_response', {
    'balance': fields.Nested(value_response, required=True, description='Balance'),
    'cluster': fields.Integer(required=True, description='Cluster id'),
    'firstTx': fields.Nested(tx_response, required=True),
    'lastTx': fields.Nested(tx_response, required=True),
    'noAddresses': fields.Integer(required=True, description='Number of adDresses'),
    'inDegree': fields.Integer(required=True, description='inDegree value'),
    'outDegree': fields.Integer(required=True, description='outDegree value'),
    'noIncomingTxs': fields.Integer(required=True, description='Incomming transactions'),
    'noOutgoingTxs': fields.Integer(required=True, description='Outgoing transactions'),
    'totalReceived': fields.Nested(value_response, required=True),
    'totalSpent': fields.Nested(value_response, required=True),
})

@api.route("/<currency>/address/<address>/cluster")
class AddressCluster(Resource):
    @api.marshal_with(cluster_response)
    def get(self, currency, address):
        """
        Returns a JSON with the details of the address cluster
        """
        if not address:
            abort(404, "Address not provided")

        address_cluster = gd.query_address_cluster(currency, address)
        return address_cluster



cluster_with_tags_response = api.model('address_cluster_with_tags_response', {
    'balance': fields.Nested(value_response, required=True, description='Balance'),
    'cluster': fields.Integer(required=True, description='Cluster id'),
    'firstTx': fields.Nested(tx_response, required=True),
    'lastTx': fields.Nested(tx_response, required=True),
    'noAddresses': fields.Integer(required=True, description='Number of adDresses'),
    'inDegree': fields.Integer(required=True, description='inDegree value'),
    'outDegree': fields.Integer(required=True, description='outDegree value'),
    'noIncomingTxs': fields.Integer(required=True, description='Incomming transactions'),
    'noOutgoingTxs': fields.Integer(required=True, description='Outgoing transactions'),
    'totalReceived': fields.Nested(value_response, required=True),
    'totalSpent': fields.Nested(value_response, required=True),
    'tags': fields.List(fields.Nested(tag_response), required=True)
})

@api.route("/<currency>/address/<address>/cluster_with_tags")
class AddressClusterWithTags(Resource):
    @api.marshal_with(cluster_with_tags_response)
    def get(self, currency, address):
        """
        Returns a JSON with edges and nodes of the address
        """
        if not address:
            abort(404, "Address not provided")

        address_cluster = gd.query_address_cluster(currency, address)
        if "cluster" in address_cluster:
            address_cluster["tags"] = gd.query_cluster_tags(
                currency, address_cluster["cluster"])
        return jsonify(address_cluster)


edge_response = api.model('edge_response', {
    "estimatedValue": fields.Nested(value_response, required=True),
    'source': fields.String(required=True, description='Source'),
    'target': fields.String(required=True, description='Target'),
    'transactions': fields.Integer(required=True, description='number of transactions')
})

node_response = api.model('node_response', {
    "balance": fields.Integer(required=True, description='Node balance'),
    "id": fields.String(required=True, description='Node Id'),
    "nodeType": fields.String(required=True, description='Node type'),
    "received": fields.Integer(required=True, description='Received amount')
})

egonet_response = api.model('address_egonet_response', {
    'edges': fields.List(fields.Nested(edge_response), required=True, description='List of edges'),
    'nodes': fields.List(fields.Nested(node_response), required=True, description='List of nodes'),
    'focusNode': fields.String(required=True, description='Focus node'),
})

@api.route("/<currency>/address/<address>/egonet")
class AddressEgonet(Resource):
    @api.doc(parser=limit_direction_parser)
    @api.marshal_with(egonet_response)
    def get(self, currency, address):
        """
        Returns a JSON with edges and nodes of the address
        """
        direction = request.args.get("direction")
        if not direction:
            direction = ""

        limit = request.args.get("limit")
        if not limit:
            limit = 50
        else:
            limit = int(limit)
        try:
            _, incoming = gd.query_address_incoming_relations(
                currency, None, address, None, int(limit))
            _, outgoing = gd.query_address_outgoing_relations(
                currency, None, address, None, int(limit))
            egoNet = gm.AddressEgoNet(
                gd.query_address(currency, address),
                gd.query_address_tags(currency, address),
                gd.query_implicit_tags(currency, address),
                incoming,
                outgoing
            )
            ret = egoNet.construct(address, direction)
        except Exception:
            ret = {}
        return ret

neighbor_response = api.model('neighbor_response', {
    "id": fields.String(required=True, description='Node Id'),
    "nodeType": fields.String(required=True, description='Node type'),
    "balance": fields.Nested(value_response, required=True),
    "received": fields.Nested(value_response, required=True, description='Received amount'),
    'noTransactions': fields.Integer(required=True, description='Number of transactions'),
    "estimatedValue": fields.Nested(value_response, required=True)
})

address_neighbors_response = api.model('address_neighbors_response', {
    'nextPage': fields.String(required=True, description='The next page'),
    'neighbors': fields.List(fields.Nested(neighbor_response), required=True, description='The list of neighbors')
})

@api.route("/<currency>/address/<address>/neighbors")
class AddressNeighbors(Resource):
    @api.doc(parser=limit_direction_parser)
    @api.marshal_with(address_neighbors_response)
    def get(self, currency, address):
        """
        Returns a JSON with edges and nodes of the address
        """
        direction = request.args.get("direction")
        if not direction:
            abort(404, "direction value missing")
        if "in" in direction:
            isOutgoing = False
        elif "out" in direction:
            isOutgoing = True
        else:
            abort(404, "invalid direction value - has to be either in or out")

        limit = request.args.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except Exception:
                abort(404, "Invalid limit value")

        pagesize = request.args.get("pagesize")
        if pagesize is not None:
            try:
                pagesize = int(pagesize)
            except Exception:
                abort(404, "Invalid pagesize value")
        page_state = request.args.get("page")
        if isOutgoing:
            (page_state, rows) = gd.query_address_outgoing_relations(
                currency, page_state, address, pagesize, limit)
        else:
            (page_state, rows) = gd.query_address_incoming_relations(
                currency, page_state, address, pagesize, limit)
        return {"nextPage": page_state.hex() if page_state is not None else None,
            "neighbors": [row.toJson() for row in rows]
        }


@api.route("/<currency>/cluster/<cluster>")
class Cluster(Resource):
    @api.marshal_with(cluster_response)
    def get(self, currency, cluster):
        """
        Returns a JSON with the details of the cluster
        """
        if not cluster:
            abort(404, "Cluster not provided")
        try:
            cluster = int(cluster)
        except Exception:
            abort(404, "Invalid cluster ID")
        cluster_obj = gd.query_cluster(currency, cluster)
        return cluster_obj.__dict__ if cluster_obj else {}


@api.route("/<currency>/cluster_with_tags/<cluster>")
class ClusterWithTags(Resource):
    @api.marshal_with(cluster_with_tags_response)
    def get(self, currency, cluster):
        """
        Returns a JSON with the tags of the cluster
        """
        if not cluster:
            abort(404, "Cluster id not provided")
        cluster_obj = gd.query_cluster(currency, cluster)
        cluster_obj.tags = gd.query_cluster_tags(currency, cluster)
        return cluster_obj.__dict__ if cluster_obj else {}


@api.route("/<currency>/cluster/<cluster>/tags")
class ClusterTags(Resource):
    @api.marshal_list_with(tag_response)
    def get(self, currency, cluster):
        """
        Returns a JSON with the tags of the cluster
        """
        if not cluster:
            abort(404, "Cluster not provided")
        try:
            cluster = int(cluster)
        except Exception:
            abort(404, "Invalid cluster ID")
        tags = gd.query_cluster_tags(currency, cluster)
        return tags

cluster_address_response = api.model('cluster_address_response', {
    'cluster': fields.Integer(required=True, description='Cluster id'),
    'address': fields.String(required=True, description='Address'),
    'address_prefix': fields.String(required=True, description='Address prefix'),
    'balance': fields.Nested(value_response, required=True),
    'firstTx': fields.Nested(tx_response, required=True),
    'lastTx': fields.Nested(tx_response, required=True),
    'inDegree': fields.Integer(required=True, description='inDegree value'),
    'outDegree': fields.Integer(required=True, description='outDegree value'),
    'noIncomingTxs': fields.Integer(required=True, description='Incomming transactions'),
    'noOutgoingTxs': fields.Integer(required=True, description='Outgoing transactions'),
    'totalReceived': fields.Nested(value_response, required=True),
    'totalSpent': fields.Nested(value_response, required=True)
})

address_transactions_response = api.model('address_transactions_response', {
    'nextPage': fields.String(required=True, description='The next page'),
    'addresses': fields.List(fields.Nested(cluster_address_response), required=True, description='The list of cluster adresses')
})

@api.route("/<currency>/cluster/<cluster>/addresses")
class ClusterAddresses(Resource):
    @api.doc(parser=limit_parser)
    @api.marshal_with(address_transactions_response)
    def get(self,currency, cluster):
        """
        Returns a JSON with the details of the addresses in the cluster
        """
        if not cluster:
            abort(404, "Cluster not provided")
        try:
            cluster = int(cluster)
        except Exception:
            abort(404, "Invalid cluster ID")
        limit = request.args.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except Exception:
                abort(404, "Invalid limit value")
        pagesize = request.args.get("pagesize")
        if pagesize is not None:
            try:
                pagesize = int(pagesize)
            except Exception:
                abort(404, "Invalid pagesize value")
        page = request.args.get("page")
        (page, addresses) = gd.query_cluster_addresses(
            currency, cluster, page, pagesize, limit)
        return {"nextPage": page.hex() if page is not None else None, "addresses": addresses}


#cluster_egonet_response = api.model('cluster_egonet_response', {
#    'edges': fields.List(fields.Nested(edge_response), required=True, description='List of edges'),
#    'nodes': fields.List(fields.Nested(node_response), required=True, description='List of nodes'),
#    'focusNode': fields.String(required=True, description='Focus node'),
#})

@api.route("/<currency>/cluster/<cluster>/egonet")
class ClusterEgonet(Resource):
    @api.doc(parser=limit_direction_parser)
    @api.marshal_with(egonet_response)
    def get(self, currency, cluster):
        """
        Returns a JSON with edges and nodes of the cluster
        """
        if not cluster:
            abort(404, "Cluster not provided")
        try:
            cluster = int(cluster)
            cluster = str(cluster)
        except Exception:
            abort(404, "Invalid cluster ID")

        direction = request.args.get("direction")
        if not direction:
            direction = ""
        limit = request.args.get("limit")
        if not limit:
            limit = 50
        else:
            try:
                limit = int(limit)
            except Exception:
                abort(404, "Invalid limit value")
        try:
            _, incoming = gd.query_cluster_incoming_relations(
                currency, None, cluster, None, int(limit))
            _, outgoing = gd.query_cluster_outgoing_relations(
                currency, None, cluster, None, int(limit))
            egoNet = gm.ClusterEgoNet(
                gd.query_cluster(currency, cluster),
                gd.query_cluster_tags(currency, cluster),
                incoming,
                outgoing
            )
            ret = egoNet.construct(cluster, direction)
        except Exception as e:
            abort(500, "%s" % e)
        return ret


@api.route("/<currency>/cluster/<cluster>/neighbors")
class ClusterNeighbors(Resource):
    def get(self, currency, cluster):
        """
        Returns a JSON with edges and nodes of the cluster
        """
        direction = request.args.get("direction")
        if not direction:
            abort(404, "direction value missing")
        if "in" in direction:
            isOutgoing = False
        elif "out" in direction:
            isOutgoing = True
        else:
            abort(404, "invalid direction value - has to be either in or out")

        limit = request.args.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except Exception:
                abort(404, "Invalid limit value")

        pagesize = request.args.get("pagesize")
        if pagesize is not None:
            try:
                pagesize = int(pagesize)
            except Exception:
                abort(404, "Invalid pagesize value")
        page_state = request.args.get("page")
        if isOutgoing:
            (page_state, rows) = gd.query_cluster_outgoing_relations(currency,
                                                                     page_state,
                                                                     cluster,
                                                                     pagesize,
                                                                     limit)
        else:
            (page_state, rows) = gd.query_cluster_incoming_relations(currency,
                                                                     page_state,
                                                                     cluster,
                                                                     pagesize,
                                                                     limit)
        return {"nextPage": page_state.hex() if page_state is not None else None,
            "neighbors": [row.toJson() for row in rows] }


@app.errorhandler(400)
def custom400(error):
    return jsonify({"message": error.description})


if __name__ == "__main__":
    gd.connect(app)
    app.run(port=9000, debug=True, processes=1)
