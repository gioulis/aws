import boto3
import argparse
import sys

# Example command to run the Script: 'python asg.py create --name test7 --vpc_id vpc-0f8e1f7aa46928acd --region ca-central-1'
# Parse Arguments
parser = argparse.ArgumentParser(description='Script for creation/deletion of Auto Scaling Groups in AWS')
subparsers = parser.add_subparsers(title="actions")
parser_create = subparsers.add_parser("create", parents=[parser], add_help=False, description="Create parser", help="Create Auto Scaling Group")
parser_create.add_argument('-n', "--name", type=str, required=True, help='Auto scaling group name')
parser_create.add_argument('-v', "--vpc_id", type=str, required=True, help='VPC id')
parser_create.add_argument('-r', "--region", type=str, required=True, help='AWS region')
parser_create.add_argument('-a', "--ami_id", type=str, required=False, help='Ami id of the image to be used in the launch template')
parser_delete = subparsers.add_parser("delete", parents=[parser], add_help=False, description="Delete parser", help="Delete Auto Scaling Group")
parser_delete.add_argument('-n', "--name", type=str, required=True, help='Auto scaling group name')
args = parser.parse_args()

if sys.argv[1] == "delete":
    asg_name = args.name + '-asg'
else:
    vpc_id = args.vpc_id
    region = args.region
    asg_name = args.name + '-asg'
    ami_id = args.ami_id



# vpc_id = "vpc-0f8e1f7aa46928acd"
region = "ca-central-1"
# asg_name = "test6"
# sec_group_id = "sg-0578a84d91b20c711"

# ami-id for slowserver custom image: ami-0b92740e01a02deb4
# ami-id for bitnami-lampstack image: ami-00e7ae0e39619b348
ami_id = "ami-0b92740e01a02deb4" # slowserver

session = boto3.Session(profile_name='nsdevadcservice')
asg_client = session.client('autoscaling', region_name=region)
ec2_client = session.client('ec2', region_name=region)
cloudwatch_client = session.client('cloudwatch', region_name=region)


step_adjustments = { "scale_up_step_adjustments" : [{
                                                    'MetricIntervalLowerBound': 0,
                                                    'ScalingAdjustment': 1
                                                    }],
                     "scale_down_step_adjustments" : [{
                                                    'MetricIntervalUpperBound': 0,
                                                    'ScalingAdjustment': -1
                                                    }]
                    }

def create_launch_template(ami_id, sec_group_id):
    # return 'lt-03722eea407e229c6'
    response = ec2_client.create_launch_template(
        LaunchTemplateData={
            'ImageId': ami_id,
            'InstanceType': 't2.micro',
            'EbsOptimized': False,
            'SecurityGroupIds': [sec_group_id],
            'TagSpecifications': [
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {
                            'Key': 'Name',
                            'Value': 'webserver',
                        },
                    ],
                },
            ],
        },
        LaunchTemplateName= args.name + '-lt'
    )
    print(response)
    return response["LaunchTemplate"]["LaunchTemplateId"]
    
def get_server_subnet(vpc_id):
    server_subnet = ec2_client.describe_subnets(Filters=[
            {
                'Name': 'vpc-id',
                'Values': [vpc_id]
            },
            {
                'Name': 'tag:aws:cloudformation:logical-id',
                'Values': ['SubnetServer*']
            }
        ]
        )
    return server_subnet["Subnets"][0]["SubnetId"]

def get_server_secgroup_id(vpc_id):
    server_secgroups = ec2_client.describe_security_groups(Filters=[
            {
                'Name': 'vpc-id',
                'Values': [vpc_id]
            },
            {
                'Name': 'tag:aws:cloudformation:logical-id',
                'Values': ['sgServer']
            }
        ]
        )
    return server_secgroups["SecurityGroups"][0]["GroupId"]

def create_cloudwatch_alarm(asg_name, alarm_name_suffix, comparison_operator, threshold, policy_arn, alarm_description):
    response = cloudwatch_client.put_metric_alarm(
        AlarmName= asg_name + alarm_name_suffix,
        ComparisonOperator= comparison_operator,
        EvaluationPeriods=1,
        MetricName='CPUUtilization',
        Namespace='AWS/EC2',
        Period=300,
        Statistic='Average',
        Threshold= threshold,
        ActionsEnabled=True,
        AlarmActions=[
            policy_arn,
        ],
        AlarmDescription= alarm_description,
        Dimensions=[
            {
            'Name': 'AutoScalingGroupName',
            'Value': asg_name
            },
        ],
        Unit='Seconds'
    )
    print(response)

def put_scaling_policy(asg_name, policy_name, step_adjustments):
    response = asg_client.put_scaling_policy(
        AutoScalingGroupName= asg_name,
        PolicyName= asg_name + policy_name,
        PolicyType='StepScaling',
        AdjustmentType='ChangeInCapacity',
        StepAdjustments= step_adjustments,
        EstimatedInstanceWarmup=300,
        Enabled=True
    )
    print(response)
    return response

def add_inbound_secgroup_rule(sec_group_id):
    response = ec2_client.authorize_security_group_ingress(
        GroupId=sec_group_id,
        IpPermissions=[
            {'IpProtocol': 'tcp',
             'FromPort': 80,
             'ToPort': 80,
             'UserIdGroupPairs': [{
                    'Description': 'HTTP access from other instances',
                    'GroupId': sec_group_id
                    }
                ]
            }
        ])
    print(response)

def create_asg(asg_name, launch_template_id, server_subnet_id):
    response = asg_client.create_auto_scaling_group(
        AutoScalingGroupName= asg_name,
        LaunchTemplate={
            'LaunchTemplateId': launch_template_id,
            'Version': '$Latest'
        },
        MinSize=1,
        MaxSize=3,
        DesiredCapacity=1,
        VPCZoneIdentifier= server_subnet_id,
        Tags=[
            {
                'ResourceId': asg_name,
                'ResourceType': 'auto-scaling-group',
                'Key': 'Name',
                'Value': asg_name,
                'PropagateAtLaunch': True
            },
        ],
    )
    print(response)
    scale_up_policy_arn = put_scaling_policy(asg_name, '-scale-up', step_adjustments["scale_up_step_adjustments"])["PolicyARN"]
    scale_down_policy_arn = put_scaling_policy(asg_name, '-scale-down', step_adjustments["scale_down_step_adjustments"])["PolicyARN"]
    create_cloudwatch_alarm(asg_name, '-highcpu-alarm', 'GreaterThanThreshold', 80.0, scale_up_policy_arn, 'Alarm when server CPU exceeds 80%'), 
    create_cloudwatch_alarm(asg_name, '-lowcpu-alarm', 'LessThanThreshold', 0.01, scale_down_policy_arn, 'Alarm when server CPU falls below 0.01%')
    add_inbound_secgroup_rule(get_server_secgroup_id(vpc_id))

def delete_launch_template(launch_template_id):
    response = ec2_client.delete_launch_template(LaunchTemplateId=launch_template_id)
    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        print(f"Launch template with id: '{launch_template_id}' has been deleted.")
    else:
        print(f"Failed to delete launch template with id: '{launch_template_id}'.")

def delete_asg(asg_name):
    response = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    launch_template_id = response["AutoScalingGroups"][0]["LaunchTemplate"]["LaunchTemplateId"]
    delete_launch_template(launch_template_id)
    response = asg_client.delete_auto_scaling_group(AutoScalingGroupName=asg_name, ForceDelete=True)
    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        print(f"Auto Scaling Group '{asg_name}' has been deleted.")
    else:
        print(f"Failed to delete Auto Scaling Group '{asg_name}'.")

if __name__ == "__main__":
    if sys.argv[1] == "create":
        create_asg(asg_name, create_launch_template(ami_id, get_server_secgroup_id(vpc_id)), get_server_subnet(vpc_id))
    elif sys.argv[1] == "delete":
        delete_asg(asg_name)
