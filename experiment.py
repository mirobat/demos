import boto3
from numpy.random import permutation
from sacred import Experiment


from sacred.observers import SqlObserver, FileStorageObserver
import sqlalchemy
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from sqlalchemy import create_engine, event, text

ex = Experiment('iris_rbf_svm')

conf ={
    'host':"database-1.cluster-ckbntugx8lms.us-east-1.rds.amazonaws.com",
    'port':'5432',
    'database':"postgres",
    'user':"postgres",
    'password':"YRM8gnBeWu!xVlzR5)3[>CDOCgyR"
}

# engine = create_engine("postgresql://")
#
# @event.listens_for(engine, "do_connect")
# def provide_token(dialect, conn_rec, cargs, cparams):
#     client = boto3.client("rds", region_name='us-east-1')
#     token = client.generate_db_auth_token(DBHostname=conf['host'], Port=conf['port'],
#                                           DBUsername=conf['user'], Region='us-east-1')
#     # set up db connection parameters, alternatively we can get these from boto3 describe_db_instances
#     cparams['host'] = conf['host']
#     cparams['port'] = conf['port']
#     cparams['user'] = conf['user']
#     cparams['password'] = token
#     cparams['database'] = conf['database']
#     print(token)
#
# my_session = sessionmaker(bind=engine)
# ex.observers.append(SqlObserver.create_from(engine, my_session))
ex.observers.append(FileStorageObserver('my_runs'))

@ex.config
def cfg():
    C = 1.0
    gamma = 0.7


@ex.automain
def run(C, gamma, _run):
    for counter in range(20):
        _run.log_scalar("training.loss", 1 * 1.5, counter)
    return 1
