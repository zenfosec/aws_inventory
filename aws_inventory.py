#!/usr/bin/env python3

'''This script will read an aws credentials file and kubeconfig file and use the credentials 
found to enumerate all ec2 instances, eks clusters, eks nodes, and eks pods in all accounts 
and regions and write the results to a csv file.

Potential Improvement: Add concurrency to instance enumeration.'''

import boto3
import csv
import datetime
import os
import argparse
import logging
from botocore.exceptions import ClientError
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from urllib3.exceptions import MaxRetryError

# Prepare Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.FileHandler('aws_inventory.log')
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Prepare argparse
parser = argparse.ArgumentParser(description='Enumerate all ec2 instances in all accounts in an aws credentials file.')
parser.add_argument('-f', '--file', help='The credentials file to use.', default=os.path.expanduser("~/.aws/credentials"))
default_output_filename = f"aws_inventory_{datetime.datetime.now().strftime('%Y%m%d%H%M')}.csv"
parser.add_argument('-o', '--output', help='The output file to write to.', default=default_output_filename)
parser.add_argument('-v', '--verbose', help='Print verbose output.', action='store_true')
args = parser.parse_args()

# Initialize variables
credentials_file = args.file
output_file = args.output
verbose = args.verbose
instance_count = 0
node_count = 0
pod_count = 0

# Get all accounts, regions, and profiles
session = boto3.Session()
credentials = session.available_profiles
unused_regions = ['af-south-1', 'ap-east-1', 'ap-south-2', 'ap-southeast-3', 'ap-southeast-4', 'ca-west-1', 'eu-central-2', 'eu-south-1', 'eu-south-2', 'il-central-1', 'me-central-1', 'me-south-1']
regions = [region for region in session.get_available_regions('ec2') if region not in unused_regions]

# Initialize csv file
csv_file = open(output_file, 'w')
csv_writer = csv.writer(csv_file)

# Write csv header including the type of resource (instance or node) the name (instance_id or node name) and the account and region
csv_writer.writerow(['Type', 'Name', 'Account / ARN', 'Region / Namespace'])

# Set KUBECONFIG environment variable
os.environ["KUBECONFIG"] = os.path.expanduser("~/.kube/config")

# Iterate through all accounts and regions
for account in credentials:
    # Skip the default account and the netsec account
    if account == 'default' or 'netsec' in account:
        continue
    logger.info('Account: %s', account)
    print('Account: ' + account)

    # Iterate through all regions
    for region in regions:
        logger.info('Region: %s', region)
        print(f'    Account: {account} Region: {region}')

        # Set up boto3 session for the account and region
        session = boto3.Session(profile_name=account, region_name=region)

        # Create ec2 client
        ec2_client = session.client('ec2')

        # Create eks client
        eks_client = session.client('eks')

        # Initialize variables to count the number of instances and nodes in the account and region
        account_instance_count = 0
        account_node_count = 0
        account_pod_count = 0

        # Enumerate all ec2 instances
        try:
            ec2_response = ec2_client.describe_instances()
            for reservation in ec2_response['Reservations']:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    logger.info('Instance: %s', instance_id)
                    print('      EC2 Instance: ' + instance_id)
                    csv_writer.writerow(['instance', instance_id, account, region])
                    instance_count += 1
                    account_instance_count += 1
        except ClientError as e:
            logger.error('Error enumerating ec2 instances: %s', e)
            print('Error enumerating ec2 instances: ' + str(e))

        # Enumerate all eks clusters
        try:
            eks_response = eks_client.list_clusters()
            for cluster in eks_response['clusters']:
                logger.info('Cluster: %s', cluster)
                print('      Cluster: ' + cluster)
                
                # Enumerate all nodes in all namespaces
                try:
                    eks_response = eks_client.list_nodegroups(clusterName=cluster)
                    for nodegroup in eks_response['nodegroups']:
                        logger.info('Nodegroup: %s', nodegroup)
                        print('        Nodegroup: ' + nodegroup)
                        eks_response = eks_client.describe_nodegroup(clusterName=cluster, nodegroupName=nodegroup)
                        for node in eks_response['nodegroup']['resources']['autoScalingGroups']:
                            logger.info('Node: %s', node['name'])
                            print('          Node: ' + node['name'])
                            csv_writer.writerow(['node', node['name'], account, region])
                            node_count += 1
                            account_node_count += 1
                except ClientError as e:
                    logger.error('Error enumerating eks nodes: %s', e)
                    print('Error enumerating eks nodes: ' + str(e))
        except ClientError as e:
            logger.error('Error enumerating eks clusters: %s', e)
            print('Error enumerating eks clusters: ' + str(e))

        # Print the number of instances and nodes in the account and region
        if account_instance_count > 0 or account_node_count > 0:
            logger.info('Instances: %s', account_instance_count)
            print(f'        Account: {account} Region: {region} EC2 Instances: ' + str(account_instance_count))
            logger.info('nodes: %s', account_node_count)
            print(f'        Account: {account} Region: {region} K8s Nodes: ' + str(account_node_count))
            print()

# Enumerate all pods in all namespaces for each context (k8s cluster)
try:
    contexts, _ = config.list_kube_config_contexts()
    for cluster in contexts:
        config.load_kube_config(context=cluster['name'])
        print("Current context:", cluster['name'])  # Print the current context
        print("  User:", cluster['context']['user'])  # Print the user associated with the current context
        print("  Cluster:", cluster['context']['cluster'])  # Print the cluster associated with the current context
        v1 = client.CoreV1Api()
        try:
            ret = v1.list_pod_for_all_namespaces(watch=False)
            for i in ret.items:
                print('    Pod: ' + i.metadata.name)
                csv_writer.writerow(['pod', i.metadata.name, cluster['name'], i.metadata.namespace])  # Add pod to the CSV file
                pod_count += 1
                account_pod_count += 1
        except client.ApiException as e:
            print("Error enumerating pods: %s\n" % e)
            print("Headers: %s" % e.headers)
            print("Body: %s" % e.body)
except ApiException as e:
    logger.error('Error enumerating pods: %s', e)
    print('Error enumerating pods: ' + str(e))

# Print the total number of instances and nodes
logger.info('Total Instances: %s', instance_count)
print('Total Instances: ' + str(instance_count))
logger.info('Total nodes: %s', node_count)
print('Total nodes: ' + str(node_count))
logger.info('Total pods: %s', pod_count)
print('Total pods: ' + str(pod_count))

# Close the csv file
csv_file.close()

# Log & print the location of the csv file
logger.info('Output file: %s', output_file)
print('Output file: ' + output_file)

# Log and print the location of the log file
logger.info('Log file: aws_inventory.log')
print('Log file: aws_inventory.log')