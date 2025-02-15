import json
import re
from functools import wraps
from flask import Flask, request, abort, Response
from flask_restplus import Api, Resource, fields
from flask_cors import CORS
from flask_jwt_extended import (JWTManager, create_access_token, create_refresh_token, jwt_required, jwt_refresh_token_required, get_jwt_identity, get_raw_jwt)
from flask_jwt_extended import exceptions as jwt_extended_exceptions
from flask_sqlalchemy import SQLAlchemy
import graphsensedao as gd
import graphsensemodel as gm


label_prefix_len = 3
address_prefix_len = transaction_prefix_len = 5
pattern = re.compile(r"[\W_]+", re.UNICODE)  # only alphanumeric chars for label


def alphanumeric_lower(expression):
    return pattern.sub("", expression).lower()


security = ["basicAuth", "apiKey"]
authorizations = {
    "basicAuth": {
        "type": "basic",
        "in": "header",
        "name": "Authorization"
    },

    "apiKey": {
        "type": "apiKey",
        "in": "header",
        "name": "Authorization"
    },
}

app = Flask(__name__)
api = Api(app=app, authorizations=authorizations, security=security, version="0.4.1", description="REST Interface for Graphsense")


'''
    Flask app configuration
'''

app.config.from_object(__name__)

with open("./config.json", "r") as fp:
    config = json.load(fp)
app.config.update(config)

app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or "some-secret-string"
app.config["SWAGGER_UI_JSONEDITOR"] = True
app.config["SQLALCHEMY_DATABASE_URI"] = app.config.get("SQLALCHEMY_DATABASE_URI") or "sqlite:////var/lib/graphsense-rest/users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["JWT_TOKEN_LOCATION"] = "headers"
app.config["JWT_SECRET_KEY"] = app.config.get("JWT_SECRET_KEY") or "jwt-secret-string"
app.config["JWT_BLACKLIST_ENABLED"] = True
app.config["JWT_BLACKLIST_TOKEN_CHECKS"] = ["access", "refresh"]
app.config["PROPAGATE_EXCEPTIONS"] = True

app.config.from_envvar("GRAPHSENSE_REST_SETTINGS", silent=True)

CORS(app, supports_credentials=True)
jwt = JWTManager(app)
db = SQLAlchemy(app)

keyspace_mapping = app.config["MAPPING"]

import authmodel

db.create_all()

'''
    Methods related to swagger argument parsing
'''

limit_parser = api.parser()
limit_parser.add_argument("limit", type=int, location="args")

limit_offset_parser = limit_parser.copy()
limit_offset_parser.add_argument("offset", type=int, location="args")

limit_query_parser = limit_parser.copy()
limit_query_parser.add_argument("q", location="args")

limit_direction_parser = limit_parser.copy()
limit_direction_parser.add_argument("direction", location="args")

direction_parser = api.parser()
direction_parser.add_argument("direction", location="args")


page_parser = api.parser()
page_parser.add_argument("page", location="args")  # TODO: find right type

search_neighbors_parser = api.parser()
search_neighbors_parser.add_argument("direction", location="args")
search_neighbors_parser.add_argument("category", location="args")
search_neighbors_parser.add_argument("ids", location="args")
search_neighbors_parser.add_argument("depth", type=int, location="args")
search_neighbors_parser.add_argument("breadth", type=int, location="args")

'''
    Methods related to user authentication
'''
@api.errorhandler(jwt_extended_exceptions.FreshTokenRequired)
def handle_expired_error():
    return {"message": "Token has expired!"}, 401


@api.errorhandler(jwt_extended_exceptions.RevokedTokenError)
def revoked_token_callback():
    return {"message": "Token has been revoked!"}, 402


@jwt.token_in_blacklist_loader
def check_if_token_in_blacklist(decrypted_token):
    jti = decrypted_token["jti"]
    return authmodel.RevokedJWTToken.is_jti_blacklisted(jti)


def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if auth:
            current_user = authmodel.GraphsenseUser.find_by_username(auth.username)
            if not current_user:
                return {"message": "Could not verify your login! User {} doesn\"t exist".format(auth.username) }, 401
            if not current_user.isAdmin:
                return {"message": "User not allowed! User {} not admin.".format(auth.username)}, 401
            if authmodel.GraphsenseUser.verify_hash(auth.password, current_user.password):
                access_token = create_access_token(identity=auth.username)
                refresh_token = create_refresh_token(identity=auth.password)
                return { "message": "Logged in as {}".format(current_user.userName), "access_token": access_token, "refresh_token": refresh_token }, 200
            else:
                return {"message": "Could not verify your login! Wrong credentials"}, 401
        return {"message": "Could not verify your login!"}, 401, {"WWW-Authenticate": "Basic realm=\"Login required\""}

    return decorated


@api.route("/login", methods=["GET"])
class UserLogin(Resource):
    @api.doc(security="basicAuth")
    @auth_required
    def get(self):
        pass


@api.route("/token_refresh", methods=["GET"])
class UserTokenRefresh(Resource):
    @jwt_refresh_token_required
    def get(self):
        current_user = get_jwt_identity()
        access_token = create_access_token(identity=current_user)
        return {"access_token": access_token}, 200


@api.route("/logout_refresh", methods=["GET"])
class UserLogoutRefresh(Resource):
    @jwt_refresh_token_required
    def get(self):
        jti = get_raw_jwt()["jti"]
        try:
            revoked_token = authmodel.RevokedJWTToken(jti=jti)
            revoked_token.add()
            return {"message": "Refresh token has been revoked!"}, 200
        except:
            return {"message": "Something went wrong"}, 500


@api.route("/logout_access", methods=["GET"])
class UserLogoutAccess(Resource):
    @jwt_required
    def get(self):
        jti = get_raw_jwt()["jti"]
        try:
            revoked_token = authmodel.RevokedJWTToken(jti=jti)
            revoked_token.add()
            return {"message": "Access token has been revoked!"}, 200
        except:
            return {"message": "Something went wrong"}, 500

'''
    Graphsense api methods
'''

value_response = api.model("value_response", {
    "eur": fields.Integer(required=True, description="EUR value"),
    "satoshi": fields.Integer(required=True, description="Satoshi value"),
    "usd": fields.Integer(required=True, description="USD value")
})


@api.route("/stats")
class Statistics(Resource):
    @jwt_required
    def get(self):
        """
        Returns a JSON with statistics of all the available currencies
        """
        statistics = dict()
        for currency in keyspace_mapping.keys():
            if currency != "tagpacks":
                statistics[currency] = gd.query_statistics(currency)
        return statistics

exchangerate = api.model("exchangerate", {
    "eur": fields.Float(required=True, description="EUR"),
    "usd": fields.Float(required=True, description="USD")
})

exchangerates_response = api.model("exchangerates_response", {
    "exchangeRates": fields.List(fields.Nested(exchangerate), required=True, description="List with exchange rates")
})


@api.route("/<currency>/exchangerates")
class ExchangeRates(Resource):
    @jwt_required
    @api.doc(parser=limit_offset_parser)
    @api.marshal_with(exchangerates_response)
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
        return {"exchangeRates": exchange_rates}


block_response = api.model("block_response", {
    "blockHash": fields.String(required=True, description="Block hash"),
    "height": fields.Integer(required=True, description="Block height"),
    "noTransactions": fields.Integer(required=True, description="Number of transactions"),
    "timestamp": fields.Integer(required=True, description="Transaction timestamp"),
})


@api.route("/<currency>/block/<int:height>")
class Block(Resource):
    @jwt_required
    @api.marshal_with(block_response)
    def get(self, currency, height):
        """
        Returns a JSON with minimal block details
        """
        block = gd.query_block(currency, height)
        if not block:
            abort(404, "Block height %d not found" % height)
        return block


blocks_response = api.model("blocks_response", {
    "Blocks": fields.List(fields.Nested(block_response), required=True, description="Block list"),
    "nextPage": fields.String(required=True, description="The next page")
})


@api.route("/<currency>/blocks")
class Blocks(Resource):
    @jwt_required
    @api.doc(parser=page_parser)
    @api.marshal_with(blocks_response)
    def get(self, currency):
        """
        Returns a JSON with 10 blocks per page
        """
        page = request.args.get("page")
        page_state = bytes.fromhex(page) if page else None
        (page_state, blocks) = gd.query_blocks(currency, page_state)
        return {"nextPage": page_state.hex() if page_state else None, "blocks": blocks}


block_transaction_response = api.model("block_transaction_response", {
    "noInputs": fields.Integer(required=True, description="Number of inputs"),
    "noOutputs": fields.Integer(required=True, description="Number of outputs"),
    "totalInput": fields.Nested(value_response, required=True, description="Total input value"),
    "totalOutput": fields.Nested(value_response, required=True, description="Total output value"),
    "txHash": fields.String(required=True, description="Transaction hash")
})

block_transactions_response = api.model("block_transactions_response", {
    "height": fields.Integer(required=True, description="Block height"),
    "txs": fields.List(fields.Nested(block_transaction_response), required=True, description="Block list")
})


@api.route("/<currency>/block/<int:height>/transactions")
class BlockTransactions(Resource):
    @jwt_required
    @api.marshal_with(block_transactions_response)
    def get(self, currency, height):
        """
        Returns a JSON with all the transactions of the block
        """
        block_transactions = gd.query_block_transactions(currency, height)
        if not block_transactions:
            abort(404, "Block height %d not found" % height)
        return block_transactions


def transactionsToCSV(jsonData):
    flatDict = {}
    def flatten(x, name=""):
        if type(x) is dict:
            for a in x:
                flatten(x[a], name + a + "_")
        else:
            flatDict[name[:-1]] = x

    txs = jsonData["txs"]
    blockHeight = jsonData["height"]
    fieldnames = []
    for tx in txs:
        flatDict["blockHeight"] = blockHeight
        flatten(tx)
        if not fieldnames:
            fieldnames = ",".join(flatDict.keys())
            yield (fieldnames + "\n")
        yield (",".join([str(item) for item in flatDict.values()]) + "\n")
        flatDict = {}


@api.route("/<currency>/block/<int:height>/transactions.csv")
class BlockTransactionsCSV(Resource):
    @jwt_required
    def get(self, currency, height):
        """
        Returns a JSON with all the transactions of the block
        """
        block_transactions = gd.query_block_transactions(currency, height)
        if not block_transactions:
            abort(404, "Block height %d not found" % height)
        return Response(transactionsToCSV(block_transactions), mimetype="text/csv")

input_output_response = api.model("input_output_response", {
    "address": fields.String(required=True, description="Address"),
    "value": fields.Nested(value_response, required=True, description="Ionput/Output value")
})

transaction_response = api.model("transaction_response", {
    "txHash": fields.String(required=True, description="Transaction hash"),
    "coinbase": fields.Boolean(required=True, description="Coinbase transaction flag"),
    "height": fields.Integer(required=True, description="Transaction height"),
    "inputs": fields.List(fields.Nested(input_output_response), required=True, description="Transaction inputs"),
    "outputs": fields.List(fields.Nested(input_output_response), required=True, description="Transaction inputs"),
    "timestamp": fields.Integer(required=True, description="Transaction timestamp"),
    "totalInput": fields.Nested(value_response, required=True),
    "totalOutput": fields.Nested(value_response, required=True),
})


@api.route("/<currency>/tx/<txHash>")
class Transaction(Resource):
    @jwt_required
    @api.marshal_with(transaction_response)
    def get(self, currency, txHash):
        """
        Returns a JSON with the details of the transaction
        """
        transaction = gd.query_transaction(currency, txHash)
        if not transaction:
            abort(404, "Transaction id %s not found" % txHash)
        return transaction


transactions_response = api.model("transactions_response", {
    "nextPage": fields.String(required=True, description="The next page"),
    "transactions": fields.List(fields.Nested(transaction_response), required=True, description="The list of transactions")
})


@api.route("/<currency>/transactions")
class Transactions(Resource):
    @jwt_required
    @api.doc(parser=page_parser)
    @api.marshal_with(transactions_response)
    def get(self, currency):
        """
        Returns a JSON with the details of 10 transactions per page
        """
        page = request.args.get("page")
        page_state = bytes.fromhex(page) if page else None

        (page_state, transactions) = gd.query_transactions(currency, page_state)
        return {
            "nextPage": page_state.hex() if page_state else None,
            "transactions": transactions
        }


search_response = api.model("search_response", {
    "addresses": fields.List(fields.String, required=True, description="The list of found addresses"),
    "transactions": fields.List(fields.String, required=True, description="The list of found transactions")
})


@api.route("/<currency>/search")
class Search(Resource):
    @jwt_required
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

        result = {"addresses": [], "transactions": []}

        # Look addresses and transactions
        if len(expression) >= address_prefix_len:
            transactions = gd.query_transaction_search(currency, expression[:transaction_prefix_len])
            addresses = gd.query_address_search(currency, expression[:address_prefix_len])

            result["addresses"] = \
                [row.address for row in addresses.current_rows if row.address.startswith(expression)][:limit]
            result["transactions"] = \
                [tx for tx in ["0"*leading_zeros + str(hex(int.from_bytes(row.tx_hash, byteorder="big")))[2:]
                               for row in transactions.current_rows] if tx.startswith(expression)][:limit]

        return result


label_search_response = api.model("label_search_response", {
    "labels": fields.List(fields.String, required=True, description="The list of found labels"),
})


@api.route("/labelsearch")
class LabelSearch(Resource):
    @jwt_required
    @api.doc(parser=limit_query_parser)
    @api.marshal_with(label_search_response)
    def get(self):
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

        result = {"labels": []}

        # Normalize label
        if len(expression) >= label_prefix_len:  # must be label_prefix_len <= address_prefix_len
            expression_norm = alphanumeric_lower(expression)
            expression_norm_prefix = expression_norm[:label_prefix_len]
            labels = gd.query_label_search(expression_norm_prefix)

            # Look for labels
            result["labels"] = list(dict.fromkeys(
                [row.label for row in labels.current_rows if row.label_norm.startswith(expression_norm)][:limit]))

        return result

tx_response = api.model("tx_response", {
    "height": fields.Integer(required=True, description="Transaction height"),
    "timestamp": fields.Integer(required=True, description="Transaction timestamp"),
    "tx_hash": fields.String(required=True, description="Transaction hash")
})


address_response = api.model("address_response", {
    "address": fields.String(required=True, description="Address"),
    "address_prefix": fields.String(required=True, description="Address prefix"),
    "balance": fields.Nested(value_response, required=True),
    "firstTx": fields.Nested(tx_response, required=True),
    "lastTx": fields.Nested(tx_response, required=True),
    "inDegree": fields.Integer(required=True, description="inDegree value"),
    "outDegree": fields.Integer(required=True, description="outDegree value"),
    "noIncomingTxs": fields.Integer(required=True, description="Incomming transactions"),
    "noOutgoingTxs": fields.Integer(required=True, description="Outgoing transactions"),
    "totalReceived": fields.Nested(value_response, required=True),
    "totalSpent": fields.Nested(value_response, required=True)
})


@api.route("/<currency>/address/<address>")
class Address(Resource):
    @jwt_required
    @api.marshal_with(address_response)
    def get(self, currency, address):
        """
        Returns a JSON with the details of the address
        """
        if not address:
            abort(404, "Address not provided")

        result = gd.query_address(currency, address)
        if not result:
            abort(404, "Address not found")
        return result


tag_response = api.model("tag_response", {
    "label": fields.String(required=True, description="Label"),
    "address": fields.String(required=True, description="Address"),
    "source": fields.String(required=True, description="Source"),
    "tagpack_uri": fields.String(required=True, description="Tagpack URI"),
    "currency": fields.String(required=True, description="Currency"),
    "lastmod": fields.String(required=True, description="Last modified"),
    "category": fields.String(required=False, description="Category")
})


@api.route("/<currency>/address/<address>/tags")
class AddressTags(Resource):
    @jwt_required
    @api.marshal_list_with(tag_response)
    def get(self, currency, address):
        """
        Returns a JSON with the explicit tags of the address
        """
        if not address:
            abort(404, "Address not provided")

        tags = gd.query_address_tags(currency, address)
        return tags


def tagsToCSV(jsonData):
    flatDict = {}
    def flatten(x, name=""):
        if type(x) is dict:
            for a in x:
                flatten(x[a], name + a + "_")
        else:
            flatDict[name[:-1]] = x

    fieldnames = []
    for tx in jsonData:
        flatten(tx)
        if not fieldnames:
            fieldnames = ",".join(flatDict.keys())
            yield (fieldnames + "\n")
        yield (",".join([str(item) for item in flatDict.values()]) + "\n")
        flatDict = {}


@api.route("/<currency>/address/<address>/tags.csv")
class AddressTagsCSV(Resource):
    @jwt_required
    def get(self, currency, address):
        """
        Returns a JSON with the explicit tags of the address
        """
        if not address:
            abort(404, "Address not provided")

        tags = gd.query_address_tags(currency, address)
        return Response(tagsToCSV(tags), mimetype="text/csv")


address_with_tags_response = api.model("address_with_tags_response", {
    "address": fields.String(required=True, description="Address"),
    "address_prefix": fields.String(required=True, description="Address prefix"),
    "balance": fields.Nested(value_response, required=True),
    "firstTx": fields.Nested(tx_response, required=True),
    "lastTx": fields.Nested(tx_response, required=True),
    "inDegree": fields.Integer(required=True, description="inDegree value"),
    "outDegree": fields.Integer(required=True, description="outDegree value"),
    "noIncomingTxs": fields.Integer(required=True, description="Incomming transactions"),
    "noOutgoingTxs": fields.Integer(required=True, description="Outgoing transactions"),
    "totalReceived": fields.Nested(value_response, required=True),
    "totalSpent": fields.Nested(value_response, required=True),
    "tags": fields.List(fields.Nested(tag_response, required=True))
})


@api.route("/<currency>/address_with_tags/<address>")
class AddressWithTags(Resource):
    @jwt_required
    @api.marshal_with(address_with_tags_response)
    def get(self, currency, address):
        """
        Returns a JSON with the transactions of the address
        """
        if not address:
            abort(404, "Address not provided")

        result = gd.query_address_with_tags(currency, address)
        if not result:
            abort(404, "Address not found")
        return result


address_transaction_response = api.model("address_transaction_response", {
    "address": fields.String(required=True, description="Address"),
    "address_prefix": fields.String(required=True, description="Address prefix"),
    "height": fields.Integer(required=True, description="Transaction height"),
    "timestamp": fields.Integer(required=True, description="Transaction timestamp"),
    "txHash": fields.String(required=True, description="Transaction hash"),
    "txIndex": fields.Integer(required=True, description="Transaction index"),
    "value": fields.Nested(value_response, required=True)
})

address_transactions_response = api.model("address_transactions_response", {
    "nextPage": fields.String(required=True, description="The next page"),
    "transactions": fields.List(fields.Nested(address_transaction_response), required=True, description="The list of transactions")
})


@api.route("/<currency>/address/<address>/transactions")
class AddressTransactions(Resource):
    @jwt_required
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

        page = request.args.get("page")
        page_state = bytes.fromhex(page) if page else None

        (page_state, rows) = gd.query_address_transactions(currency, page_state, address, pagesize, limit)
        txs = [gm.AddressTransactions(
                   row, gd.query_exchange_rate_for_height(currency, row.height)
               ).__dict__
               for row in rows]
        return {
            "nextPage": page_state.hex() if page_state else None,
            "transactions": txs
        }


@api.route("/<currency>/address/<address>/implicitTags")
class AddressImplicitTags(Resource):
    @jwt_required
    @api.marshal_list_with(tag_response)
    def get(self, currency, address):
        """
        Returns a JSON with the implicit tags of the address
        """
        if not address:
            abort(404, "Address not provided")

        implicit_tags = gd.query_implicit_tags(currency, address)
        return implicit_tags


cluster_response = api.model("address_cluster_response", {
    "balance": fields.Nested(value_response, required=True, description="Balance"),
    "cluster": fields.Integer(required=True, description="Cluster id"),
    "firstTx": fields.Nested(tx_response, required=True),
    "lastTx": fields.Nested(tx_response, required=True),
    "noAddresses": fields.Integer(required=True, description="Number of adDresses"),
    "inDegree": fields.Integer(required=True, description="inDegree value"),
    "outDegree": fields.Integer(required=True, description="outDegree value"),
    "noIncomingTxs": fields.Integer(required=True, description="Incomming transactions"),
    "noOutgoingTxs": fields.Integer(required=True, description="Outgoing transactions"),
    "totalReceived": fields.Nested(value_response, required=True),
    "totalSpent": fields.Nested(value_response, required=True),
})


@api.route("/<currency>/address/<address>/cluster")
class AddressCluster(Resource):
    @jwt_required
    @api.marshal_with(cluster_response)
    def get(self, currency, address):
        """
        Returns a JSON with the details of the address cluster
        """
        if not address:
            abort(404, "Address not provided")

        address_cluster = gd.query_address_cluster(currency, address)
        return address_cluster


cluster_with_tags_response = api.model("address_cluster_with_tags_response", {
    "balance": fields.Nested(value_response, required=True, description="Balance"),
    "cluster": fields.Integer(required=True, description="Cluster id"),
    "firstTx": fields.Nested(tx_response, required=True),
    "lastTx": fields.Nested(tx_response, required=True),
    "noAddresses": fields.Integer(required=True, description="Number of adDresses"),
    "inDegree": fields.Integer(required=True, description="inDegree value"),
    "outDegree": fields.Integer(required=True, description="outDegree value"),
    "noIncomingTxs": fields.Integer(required=True, description="Incomming transactions"),
    "noOutgoingTxs": fields.Integer(required=True, description="Outgoing transactions"),
    "totalReceived": fields.Nested(value_response, required=True),
    "totalSpent": fields.Nested(value_response, required=True),
    "tags": fields.List(fields.Nested(tag_response), required=True)
})


@api.route("/<currency>/address/<address>/cluster_with_tags")
class AddressClusterWithTags(Resource):
    @jwt_required
    @api.marshal_with(cluster_with_tags_response)
    def get(self, currency, address):
        """
        Returns a JSON with edges and nodes of the address
        """
        if not address:
            abort(404, "Address not provided")

        address_cluster = gd.query_address_cluster(currency, address)
        if "cluster" in address_cluster:
            address_cluster["tags"] = gd.query_cluster_tags(currency, address_cluster["cluster"])
        return address_cluster


neighbor_response = api.model("neighbor_response", {
    "id": fields.String(required=True, description="Node Id"),
    "nodeType": fields.String(required=True, description="Node type"),
    "balance": fields.Nested(value_response, required=True),
    "received": fields.Nested(value_response, required=True, description="Received amount"),
    "noTransactions": fields.Integer(required=True, description="Number of transactions"),
    "estimatedValue": fields.Nested(value_response, required=True)
})

neighbors_response = api.model("address_neighbors_response", {
    "nextPage": fields.String(required=True, description="The next page"),
    "neighbors": fields.List(fields.Nested(neighbor_response), required=True, description="The list of neighbors")
})


@api.route("/<currency>/address/<address>/neighbors")
class AddressNeighbors(Resource):
    @jwt_required
    @api.doc(parser=limit_direction_parser)
    @api.marshal_with(neighbors_response)
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

        page = request.args.get("page")
        page_state = bytes.fromhex(page) if page else None

        if isOutgoing:
            (page_state, rows) = gd.query_address_outgoing_relations(currency, page_state, address, pagesize, limit)
        else:
            (page_state, rows) = gd.query_address_incoming_relations(currency, page_state, address, pagesize, limit)
        return {"nextPage": page_state.hex() if page_state else None,
                "neighbors": [row.toJson() for row in rows]}


def neighboursToCSV(query_function, currency, cluster, pagesize, limit, page_state = None):
    fieldnames = []
    flatDict = {}
    while True:
        (page_state, rows) = query_function(currency, page_state, cluster, pagesize, limit)

        def flatten(item, name=""):
            if type(item) is dict:
                for sub_item in item:
                    flatten(item[sub_item], name + sub_item + "_")
            else:
                flatDict[name[:-1]] = item

        for row in rows:
            #for item in row.toJson():
            flatten(row.toJson())
            if not fieldnames:
                fieldnames = ",".join(flatDict.keys())
                yield (fieldnames + "\n")
            yield (",".join([str(item) for item in flatDict.values()]) + "\n")
            flatDict = {}

        if not page_state:
            break

@api.route("/<currency>/address/<address>/neighbors.csv")
class AddressNeighborsCSV(Resource):
    @jwt_required
    @api.doc(parser=limit_direction_parser)
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

        if isOutgoing:
            query_function = gd.query_address_outgoing_relations
        else:
            query_function = gd.query_address_incoming_relations

        return Response(neighboursToCSV(query_function, currency, address, pagesize, limit), mimetype="text/csv")


@api.route("/<currency>/cluster/<cluster>")
class Cluster(Resource):
    @jwt_required
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
        if not cluster_obj:
            abort(404, "Cluster not found")
        return cluster_obj


@api.route("/<currency>/cluster_with_tags/<cluster>")
class ClusterWithTags(Resource):
    @jwt_required
    @api.marshal_with(cluster_with_tags_response)
    def get(self, currency, cluster):
        """
        Returns a JSON with the tags of the cluster
        """
        if not cluster:
            abort(404, "Cluster id not provided")
        cluster_obj = gd.query_cluster(currency, cluster)
        if not cluster_obj:
            abort(404, "Cluster not found")
        cluster_obj.tags = gd.query_cluster_tags(currency, cluster)
        return cluster_obj


@api.route("/<currency>/cluster/<cluster>/tags")
class ClusterTags(Resource):
    @jwt_required
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


@api.route("/<currency>/cluster/<cluster>/tags.csv")
class ClusterTagsCSV(Resource):
    @jwt_required
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

        return Response(tagsToCSV(tags), mimetype="text/csv")



cluster_address_response = api.model("cluster_address_response", {
    "cluster": fields.Integer(required=True, description="Cluster id"),
    "address": fields.String(required=True, description="Address"),
    "address_prefix": fields.String(required=True, description="Address prefix"),
    "balance": fields.Nested(value_response, required=True),
    "firstTx": fields.Nested(tx_response, required=True),
    "lastTx": fields.Nested(tx_response, required=True),
    "inDegree": fields.Integer(required=True, description="inDegree value"),
    "outDegree": fields.Integer(required=True, description="outDegree value"),
    "noIncomingTxs": fields.Integer(required=True, description="Incomming transactions"),
    "noOutgoingTxs": fields.Integer(required=True, description="Outgoing transactions"),
    "totalReceived": fields.Nested(value_response, required=True),
    "totalSpent": fields.Nested(value_response, required=True)
})

address_transactions_response = api.model("address_transactions_response", {
    "nextPage": fields.String(required=True, description="The next page"),
    "addresses": fields.List(fields.Nested(cluster_address_response), required=True, description="The list of cluster adresses")
})

@api.route("/<currency>/cluster/<cluster>/addresses")
class ClusterAddresses(Resource):
    @jwt_required
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
        page_state = bytes.fromhex(page) if page else None
        (page, addresses) = gd.query_cluster_addresses(
            currency, cluster, page_state, pagesize, limit)
        return {"nextPage": page.hex() if page is not None else None, "addresses": addresses}


@api.route("/<currency>/cluster/<cluster>/neighbors")
class ClusterNeighbors(Resource):
    @jwt_required
    @api.doc(parser=limit_direction_parser)
    @api.marshal_with(neighbors_response)
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

        page = request.args.get("page")
        page_state = bytes.fromhex(page) if page else None

        if isOutgoing:
            (page_state, rows) = gd.query_cluster_outgoing_relations(currency, page_state, cluster, pagesize, limit)
        else:
            (page_state, rows) = gd.query_cluster_incoming_relations(currency, page_state, cluster, pagesize, limit)

        return {"nextPage": page_state.hex() if page_state else None,
                "neighbors": [row.toJson() for row in rows]}


@api.route("/<currency>/cluster/<cluster>/neighbors.csv")
class ClusterNeighborsCSV(Resource):
    @jwt_required
    @api.doc(parser=limit_direction_parser)
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

        if isOutgoing:
            query_function = gd.query_cluster_outgoing_relations
        else:
            query_function = gd.query_cluster_incoming_relations

        return Response(neighboursToCSV(query_function, currency, cluster, pagesize, limit), mimetype="text/csv")


label_response = api.model("label_response", {
    "label": fields.String(required=True, description="Label"),
    "label_norm": fields.String(required=True, description="Normalized label"),
    "address_count": fields.Integer(required=True, description="Number of addresses for the label"),
})


@api.route("/label/<label>")
class Label(Resource):
    @jwt_required
    @api.marshal_with(label_response)
    def get(self, label):
        """
        Returns a JSON with the details of the label
        """
        if not label:
            abort(404, "Label not provided")
        label_norm = alphanumeric_lower(label)
        label_norm_prefix = label_norm[:label_prefix_len]
        result = gd.query_label(label_norm_prefix, label_norm)
        if not result:
            abort(404, "Label not found")

        return result


@api.route("/label/<label>/tags")
class LabelTags(Resource):
    @jwt_required
    @api.marshal_list_with(tag_response)
    def get(self, label):
        """
        Returns a JSON with the tags with the label
        """
        if not label:
            abort(404, "Label not provided")
        label_norm = alphanumeric_lower(label)
        label_norm_prefix = label_norm[:label_prefix_len]
        result = gd.query_tags(label_norm_prefix, label_norm)
        if not result:
            abort(404, "Label not found")
        return result


def search_neighbors_recursive(depth = 7):
    mapping = {
        "node": fields.Nested(cluster_with_tags_response, required=True, description="Node"),
        "matchingAddresses": fields.List(fields.Nested(address_with_tags_response, required=True, description="Addresses contained in cluster node that matched the search query (if any)")),
        "relation": fields.Nested(neighbor_response, required=True, description="Relation to parent node")
    }

    if depth:
        mapping["paths"] = fields.List(fields.Nested(search_neighbors_recursive(depth-1), required=True))

    return api.model("mapping%s" % depth, mapping)

maxdepth = 7
search_neighbors_response = api.model("search_neighbors_response_depth_" + str(maxdepth), {
        "paths": fields.List(fields.Nested(search_neighbors_recursive(maxdepth), required=True))
    })


@api.route("/<currency>/cluster/<cluster>/search")
class ClusterSearchNeighbors(Resource):
    @jwt_required
    @api.doc(parser=search_neighbors_parser)
    @api.marshal_with(search_neighbors_response)
    def get(self, currency, cluster):
        try:
            # depth search
            depth = int(request.args.get("depth") or 1)
            # breadth search
            breadth = int(request.args.get("breadth") or 16)
        except:
            abort(400, "Invalid depth or breadth")

        if depth > maxdepth:
            abort(400, "Depth must not exceed " + str(maxdepth))

        direction = request.args.get("direction")
        if not direction:
            abort(400, "direction value missing")
        if "in" in direction:
            isOutgoing = False
        elif "out" in direction:
            isOutgoing = True
        else:
            abort(400, "invalid direction value - has to be either in or out")

        category = request.args.get("category")
        ids = request.args.get("addresses")
        if ids:
            ids = [ {"address" : address, "cluster" : gd.query_address_cluster_id(currency, address)} for address in ids.split(",")]

        result = gd.query_cluster_search_neighbors(currency, cluster, isOutgoing, category, ids, breadth, depth)
        return {"paths": result}


@app.errorhandler(400)
def custom400(error):
    return {"message": error.description}


if __name__ == "__main__":
    gd.connect(app)
    app.run(port=9000, debug=True, processes=1)
