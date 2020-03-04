#!/usr/bin/env python3

from aws_cdk import core

from cluster.cluster_stack import (
    HelperStack,
    RepoStack,
    BaseStack,
    EsDockerComposeStack,
    EsDockerStack,
)

app = core.App()

helper_stack = HelperStack(app, "helper")

repo_stack = RepoStack(app, "repo", helper_stack.remove_repository_lambda_arn)
base = BaseStack(app, "base")
EsDockerComposeStack(app, "es-dockercompose", base.vpc, base.cluster)
EsDockerStack(app, "es-docker", base.vpc, base.cluster, repo_stack.es_repository)


app.synth()
