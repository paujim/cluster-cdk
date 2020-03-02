import boto3
import logging
import cfnresponse

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)


def handler(event, context):
    LOG.info('REQUEST RECEIVED: %s', event)
    custom_resource_ID = 'f7d0f730-4e01-1108-9c0d-fa7ae010b0bc'
    response = 'UNKNOWN'
    try:
        if event['RequestType'] == 'Create':
            LOG.info('CREATE')
        elif event['RequestType'] == 'Update':
            LOG.info('UPDATE')
        elif event['RequestType'] == 'Delete':
            LOG.info('DELETE')
            repository_name = event['ResourceProperties']['RepositoryName']
            LOG.info(repository_name)
            client = boto3.client('ecr')
            response = client.delete_repository(
                repositoryName=repository_name,
                force=True,
            )
            response = 'SUCCESS'
        else:
            LOG.info('FAILED')
            response = 'FAILED'

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                         'Response': response}, custom_resource_ID)
    except Exception as inst:  # pylint: disable=W0702
        LOG.info('ERROR:')
        LOG.info(inst)
        cfnresponse.send(event, context, cfnresponse.SUCCESS,
                         {'Response': str(inst)}, custom_resource_ID)
