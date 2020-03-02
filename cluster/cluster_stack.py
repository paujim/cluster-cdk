import constants
import typing
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_iam as iam,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_logs as logs,
    aws_autoscaling as autoscaling,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticloadbalancingv2 as elbv2,
    aws_servicediscovery as servicediscovery,
    aws_cloudformation as cloudformation,
    aws_lambda as _lambda,
    aws_codebuild as codebuild,
    aws_codecommit as codecommit,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_cloudformation as cfn,
    core,
)


class HelperStack(core.Stack):
    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        inline_policy = iam.PolicyStatement(
            # principals=[ iam.ServicePrincipal('lambda.amazonaws.com') ],
            actions=['ecr:DeleteRepository'],
            effect=iam.Effect.ALLOW,
            resources=['*']
        )
        remove_repository_lambda = _lambda.Function(
            scope=self,
            id='REMOVE-REPOSITORY-LAMBDA',
            handler='delete-ecr-repository.handler',
            runtime=_lambda.Runtime.PYTHON_3_7,
            code=_lambda.Code.from_asset(path='lambda'),
            initial_policy=[inline_policy]
        )
        self.remove_repository_lambda_arn = remove_repository_lambda.function_arn
        core.CfnOutput(
            scope=self,
            id="REMOVE-REPO-LAMBDA-ARN",
            value=remove_repository_lambda.function_arn,
        )


class RemoveRepoCustomResource(core.Construct):
    def __init__(self, scope: core.Construct, id: str, remove_repository_lambda_arn: str, repository_name: str) -> None:
        super().__init__(scope, id)

        remove_repository_lambda = _lambda.Function.from_function_arn(
            scope=self,
            id='REMOVE-REPOSITORY-LAMBDA',
            function_arn=remove_repository_lambda_arn
        )
        resource = cfn.CustomResource(
            scope=self,
            id="RESOURCE-REMOVE-REPOSITORY",
            provider=cfn.CustomResourceProvider.from_lambda(
                remove_repository_lambda),
            properties={'RepositoryName': repository_name},
        )
        self.response = resource.get_att("Response").to_string()


class RepoStack(core.Stack):
    def __init__(self, scope: core.Construct, id: str, remove_repository_lambda_arn: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        es_repository = ecr.Repository(
            scope=self,
            id="ECR-REPO",
            repository_name=constants.ES_REPO_NAME,
        )
        es_repository.add_lifecycle_rule(
            max_image_age=core.Duration.days(amount=30))

        self.es_repository = es_repository

        resource = RemoveRepoCustomResource(
            scope=self,
            id="REMOVE-RESOURCE",
            remove_repository_lambda_arn=remove_repository_lambda_arn,
            repository_name=es_repository.repository_name)

        core.CfnOutput(
            scope=self,
            id="ECR-REPO-URI",
            value=es_repository.repository_uri,
        )


class BaseStack(core.Stack):
    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        vpc = ec2.Vpc(
            scope=self,
            id="ECS-VPC",
            enable_dns_hostnames=True,
            enable_dns_support=True,
            cidr=constants.VPC_CIDR,
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name='dmz',
                    subnet_type=ec2.SubnetType.PUBLIC
                ),
                ec2.SubnetConfiguration(
                    name='trust',
                    subnet_type=ec2.SubnetType.PRIVATE
                ),
                # ec2.SubnetConfiguration(
                #     name='isolated',
                #     subnet_type=ec2.SubnetType.ISOLATED,
                # ),
            ],
        )
        self.vpc = vpc

        cluster = ecs.Cluster(
            scope=self,
            id='ECS-CLUSTER',
            vpc=vpc,
            cluster_name=constants.ECS_CLUSTER_NAME
        )

        asg = autoscaling.AutoScalingGroup(
            self,
            "ASG",
            vpc=vpc,
            key_name=constants.SSH_KEY_NAME,
            block_devices=[
                autoscaling.BlockDevice(
                    device_name="/dev/xvda",
                    volume=autoscaling.BlockDeviceVolume(ebs_device=autoscaling.EbsDeviceProps(
                        delete_on_termination=True,
                        volume_type=autoscaling.EbsDeviceVolumeType.GP2,
                        volume_size=100,
                    )),
                ),
                autoscaling.BlockDevice(
                    device_name="/dev/xvdb",
                    volume=autoscaling.BlockDeviceVolume(ebs_device=autoscaling.EbsDeviceProps(
                        delete_on_termination=True,
                        volume_type=autoscaling.EbsDeviceVolumeType.GP2,
                        volume_size=50,
                    )),
                ),
            ],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE),
            instance_type=ec2.InstanceType("t2.xlarge"),
            machine_image=ecs.EcsOptimizedAmi(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            min_capacity=2,
        )

        user_data = '''
sudo yum -y update && sudo sysctl -w vm.max_map_count=262144
mkdir -p /usr/share/elasticsearch/data/
chown -R 1000.1000 /usr/share/elasticsearch/data/
sudo mount /dev/xvdb /usr/share/elasticsearch/data/
'''
        asg.add_user_data(user_data)
        cluster.add_auto_scaling_group(asg)

        self.cluster = cluster


class EsDockerComposeStack(core.Stack):

    def __init__(self, scope: core.Construct, id: str, vpc: ec2.Vpc, cluster: ecs.Cluster, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        elastic_cluster_task_def = ecs.Ec2TaskDefinition(
            scope=self,
            id="ES-TASK-DEF",
            network_mode=ecs.NetworkMode.BRIDGE,
        )

        elastic = ecs.ContainerDefinition(
            scope=self,
            id=constants.ES_CONTAINER_NAME,
            start_timeout=core.Duration.seconds(amount=30),
            task_definition=elastic_cluster_task_def,
            memory_limit_mib=4024,
            essential=True,
            image=ecs.ContainerImage.from_registry(
                name="docker.elastic.co/elasticsearch/elasticsearch:6.8.6"),
            environment={
                "cluster.name": constants.ES_CLUSTER_NAME,
                "bootstrap.memory_lock": "true",
                # "discovery.zen.ping.unicast.hosts": "elasticsearch",
                "node.name": constants.ES_CONTAINER_NAME,
                "node.master": "true",
                "node.data": "true",
                "ES_JAVA_OPTS": "-Xms2g -Xmx2g",
            },
            logging=ecs.AwsLogDriver(
                stream_prefix="ES",
                log_retention=logs.RetentionDays.ONE_DAY,
            ),
        )
        elastic.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.NOFILE, hard_limit=65535, soft_limit=65535))
        elastic.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.MEMLOCK, hard_limit=-1, soft_limit=-1))

        elastic.add_port_mappings(ecs.PortMapping(container_port=9200))
        elastic.add_port_mappings(ecs.PortMapping(container_port=9300))

        #####################################################
        node = ecs.ContainerDefinition(
            scope=self,
            id=constants.ES_NODE_CONTAINER_NAME,
            start_timeout=core.Duration.seconds(amount=40),
            task_definition=elastic_cluster_task_def,
            memory_limit_mib=4024,
            essential=True,
            image=ecs.ContainerImage.from_registry(
                name="docker.elastic.co/elasticsearch/elasticsearch:6.8.6"),
            environment={
                "cluster.name": constants.ES_CLUSTER_NAME,
                "bootstrap.memory_lock": "true",
                "discovery.zen.ping.unicast.hosts": constants.ES_CONTAINER_NAME,
                "node.name": constants.ES_NODE_CONTAINER_NAME,
                "node.master": "false",
                "node.data": "true",
                "ES_JAVA_OPTS": "-Xms2g -Xmx2g",
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="NODE",
                log_retention=logs.RetentionDays.ONE_DAY,
            ))

        node.add_port_mappings(ecs.PortMapping(container_port=9200))
        node.add_port_mappings(ecs.PortMapping(container_port=9300))

        node.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.NOFILE, hard_limit=65536, soft_limit=65536))
        node.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.MEMLOCK, hard_limit=-1, soft_limit=-1))
        node.add_link(container=elastic, alias=constants.ES_CONTAINER_NAME)

        #####################################################

        ecs_service = ecs.Ec2Service(
            scope=self,
            id="ES-SERVICE",
            cluster=cluster,
            task_definition=elastic_cluster_task_def,
            desired_count=1,
            service_name=constants.ECS_ES_SERVICE,
        )

        lb = elbv2.ApplicationLoadBalancer(
            scope=self,
            id="ELB",
            vpc=vpc,
            internet_facing=True,
        )
        listener = lb.add_listener(
            id="LISTENER",
            port=80,
        )
        ecs_service.register_load_balancer_targets(
            ecs.EcsTarget(
                new_target_group_id="TARGET-GRP",
                container_name=elastic.container_name,
                # container_port=9200,
                listener=ecs.ListenerConfig.application_listener(
                    listener=listener,
                    protocol=elbv2.ApplicationProtocol.HTTP),
            ))

        core.CfnOutput(
            scope=self,
            id="DNS-NAME",
            value=lb.load_balancer_dns_name,
        )


class EsDockerStack(core.Stack):

    def __init__(self, scope: core.Construct, id: str, vpc: ec2.Vpc, cluster: ecs.Cluster, repository: ecr.Repository, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        namespace = servicediscovery.PrivateDnsNamespace(
            scope=self,
            id="PRIVATE-DNS",
            vpc=vpc,
            name="private",
            description="a private dns"
        )

        sg = ec2.SecurityGroup(
            scope=self,
            id="SG",
            vpc=vpc,
            allow_all_outbound=True,
            description="open 9200 and 9300 ports",
            security_group_name="es-group"
        )
        sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(port=9200),
        )
        sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(port=9300),
        )

        #####################################################
        elastic_task_def = ecs.Ec2TaskDefinition(
            scope=self,
            id="ES-TASK-DEF",
            network_mode=ecs.NetworkMode.AWS_VPC,
            volumes=[ecs.Volume(
                name="esdata",
                host=ecs.Host(source_path="/usr/share/elasticsearch/data"),
            )],
        )

        elastic = ecs.ContainerDefinition(
            scope=self,
            id=constants.ES_CONTAINER_NAME,
            start_timeout=core.Duration.seconds(amount=30),
            task_definition=elastic_task_def,
            memory_limit_mib=4500,
            essential=True,
            image=ecs.ContainerImage.from_ecr_repository(
                repository=repository, tag='latest'),
            environment={
                "cluster.name": constants.ES_CLUSTER_NAME,
                "bootstrap.memory_lock": "true",
                # "discovery.zen.ping.unicast.hosts": "elasticsearch",
                "node.name": constants.ES_CONTAINER_NAME,
                "node.master": "true",
                "node.data": "true",
                "ES_JAVA_OPTS": "-Xms4g -Xmx4g",
            },
            logging=ecs.AwsLogDriver(
                stream_prefix="ES",
                log_retention=logs.RetentionDays.ONE_DAY,
            ),
        )
        elastic.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.NOFILE, hard_limit=65535, soft_limit=65535))
        elastic.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.MEMLOCK, hard_limit=-1, soft_limit=-1))

        elastic.add_port_mappings(ecs.PortMapping(container_port=9200))
        elastic.add_port_mappings(ecs.PortMapping(container_port=9300))

        elastic.add_mount_points(ecs.MountPoint(
            container_path="/usr/share/elasticsearch/data",
            source_volume="esdata",
            read_only=False,
        ))
        # elastic.add_volumes_from(ecs.VolumeFrom(
        #     source_container="esdata",
        #     read_only=False,
        #     ))

        es_service = ecs.Ec2Service(
            scope=self,
            id="ES-SERVICE",
            cluster=cluster,
            task_definition=elastic_task_def,
            desired_count=1,
            service_name="ES",
            security_group=sg,
        )

        es_lb = elbv2.ApplicationLoadBalancer(
            scope=self,
            id="ES-ELB",
            vpc=vpc,
            internet_facing=True,
        )
        es_listener = es_lb.add_listener(
            id="ES-LISTENER",
            port=80,
        )
        es_service.register_load_balancer_targets(
            ecs.EcsTarget(
                new_target_group_id="ES-GRP",
                container_name=elastic.container_name,
                listener=ecs.ListenerConfig.application_listener(
                    listener=es_listener,
                    protocol=elbv2.ApplicationProtocol.HTTP),
            ))

        service = es_service.enable_cloud_map(
            cloud_map_namespace=namespace,
            dns_record_type=servicediscovery.DnsRecordType.A,
            # dns_ttl=core.Duration.seconds(amount=30),
            failure_threshold=1,
            name="elastic",
        )

        core.CfnOutput(
            scope=self,
            id="DNS-ES",
            value=es_lb.load_balancer_dns_name,
        )

        #####################################################

        node_task_def = ecs.Ec2TaskDefinition(
            scope=self,
            id="NODE-TASK-DEF",
            network_mode=ecs.NetworkMode.AWS_VPC,
            volumes=[ecs.Volume(
                name="esdata",
                host=ecs.Host(source_path="/usr/share/elasticsearch/data"),
            )],
        )

        node = ecs.ContainerDefinition(
            scope=self,
            id=constants.ES_NODE_CONTAINER_NAME,
            start_timeout=core.Duration.seconds(amount=40),
            task_definition=node_task_def,
            memory_limit_mib=4500,
            essential=True,
            image=ecs.ContainerImage.from_ecr_repository(
                repository=repository, tag='latest'),
            environment={
                "cluster.name": constants.ES_CLUSTER_NAME,
                "bootstrap.memory_lock": "true",
                "discovery.zen.ping.unicast.hosts": "elastic.private",
                "node.name": constants.ES_NODE_CONTAINER_NAME,
                "node.master": "false",
                "node.data": "true",
                "ES_JAVA_OPTS": "-Xms4g -Xmx4g",
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="NODE",
                log_retention=logs.RetentionDays.ONE_DAY,
            ))

        node.add_port_mappings(ecs.PortMapping(container_port=9200))
        node.add_port_mappings(ecs.PortMapping(container_port=9300))

        node.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.NOFILE, hard_limit=65536, soft_limit=65536))
        node.add_ulimits(ecs.Ulimit(
            name=ecs.UlimitName.MEMLOCK, hard_limit=-1, soft_limit=-1))
        node.add_mount_points(ecs.MountPoint(
            container_path="/usr/share/elasticsearch/data",
            source_volume="esdata",
            read_only=False,
        ))

        node_service = ecs.Ec2Service(
            scope=self,
            id="ES-NODE-SERVICE",
            cluster=cluster,
            task_definition=node_task_def,
            desired_count=1,
            service_name="NODE",
            security_group=sg,
        )

        node_lb = elbv2.ApplicationLoadBalancer(
            scope=self,
            id="NODE-ELB",
            vpc=vpc,
            internet_facing=True,
        )
        node_listener = node_lb.add_listener(
            id="NODE-LISTENER",
            port=80,
        )
        node_service.register_load_balancer_targets(
            ecs.EcsTarget(
                new_target_group_id="NODE-GRP",
                container_name=node.container_name,
                listener=ecs.ListenerConfig.application_listener(
                    listener=node_listener,
                    protocol=elbv2.ApplicationProtocol.HTTP),
            ))
        core.CfnOutput(
            scope=self,
            id="DNS-NODE",
            value=node_lb.load_balancer_dns_name,
        )
        #####################################################
