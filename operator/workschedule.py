import kopf
from kubernetes import client as klient
from kubernetes import config as kconfig
from datetime import datetime
from pytz import timezone, utc
from typing import Optional, Dict

# Initialize Kubernetes client
kconfig.load_incluster_config()
v1 = klient.AppsV1Api()
custom_api = klient.CustomObjectsApi()

# Constants
API_GROUP = 'example.com'
API_VERSION = 'v1'

POLICY_NAME_ANNOTATION_KEY = f'workschedule.{API_GROUP}/policy'
REPLICAS_ANNOTATION_KEY = f'workschedule.{API_GROUP}/replicas'


def get_work_schedule(ws_policy: str, logger) -> Optional[Dict]:
    """
    Retrieves the WorkSchedule object from Kubernetes Custom Resources.
    """
    try:
        ws = custom_api.get_cluster_custom_object(
            group=API_GROUP,
            version=API_VERSION,
            plural='workschedules',
            name=ws_policy
        )
        return ws
    except klient.exceptions.ApiException as e:
        if e.status == 404:
            logger.warning(f"WorkSchedule {ws_policy} not found")
        else:
            logger.error(f"Failed to get WorkSchedule {ws_policy}: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return None


@kopf.timer('deployments',
            interval=60, # 1 minute
            annotations={POLICY_NAME_ANNOTATION_KEY: kopf.PRESENT},
            )
async def deployment_timer_handler(meta: Dict, spec: Dict, **kwargs):
    """
    Timer handler function to scale deployments up or down based on WorkSchedule.
    """
    # Get logger from Kopf context
    logger = kwargs['logger']
    current_replicas = spec.get('replicas')

    # Get the WorkSchedule name from the deployment annotations
    ws_policy = meta['annotations'][POLICY_NAME_ANNOTATION_KEY]
    logger.info(f"Deployment has WorkSchedule {ws_policy}")

    # Get the WorkSchedule object
    ws = get_work_schedule(ws_policy, logger)
    if not ws:
        return

    # Parse the WorkSchedule object
    start_time = ws['spec']['startTime']
    end_time = ws['spec']['endTime']
    ws_tz = ws['spec']['timeZone']

    # Get and localize the current time
    current_time_obj = datetime.now(timezone(ws_tz)).time()
    logger.info(f"Current time in {ws_tz}: {current_time_obj}")

    # Convert start and end time to time objects
    start_time_obj = datetime.strptime(start_time, '%H:%M').time()
    end_time_obj = datetime.strptime(end_time, '%H:%M').time()

    logger.debug(
        f"WorkSchedule working hours: {start_time_obj}-{end_time_obj}")

    # Check if the current time is within the working hours
    try:
        # make sure endTime is always greater than startTime
        if end_time_obj < start_time_obj:
            logger.error(f'endTime {end_time_obj} value cannot be earlier than startTime {start_time_obj}')
            return
        
        # make sure endTime is not equal to startTime
        if end_time_obj == start_time_obj:
            logger.error(f'endTime {end_time_obj} value cannot be equal to startTime {start_time_obj}')
            return
        
        if current_time_obj < start_time_obj or current_time_obj > end_time_obj:
            logger.info(
                f"Current time {current_time_obj} is outside the working hours {start_time_obj}-{end_time_obj}")
            go_to_sleep(meta, current_replicas, logger)
        else:
            logger.info(
                f"Current time {current_time_obj} is within the working hours {start_time_obj}-{end_time_obj}")
            wake_up(meta, current_replicas, logger)
    except Exception as e:
        logger.error(f"Failed to process WorkSchedule: {e}")
        return

    return


def go_to_sleep(meta: Dict, current_replicas: int, logger):
    """
    Scales down the deployment to zero replicas.
    """
    if current_replicas == 0:
        logger.debug("Deployment is already scaled down")
        return

    logger.info("Scaling down deployment")

    # Store current replica count in the annotations
    logger.debug(
        f"Storing current replica count {current_replicas} in the annotations")
    patch_deployment(
        name=meta['name'],
        namespace=meta['namespace'],
        body={
            "metadata": {
                "annotations": {
                    REPLICAS_ANNOTATION_KEY: str(current_replicas)
                }
            }
        },
        logger=logger
    )

    # Scale down the deployment
    return patch_deployment(
        name=meta['name'],
        namespace=meta['namespace'],
        body={
            "spec": {
                "replicas": 0
            }
        },
        logger=logger
    )


def wake_up(meta: Dict, current_replicas: int, logger):
    """
    Scales up the deployment to the previously stored replica count.
    """
    if current_replicas > 0:
        logger.debug("Deployment is already scaled up")
        return

    # Get the replica count from the annotations
    replicas = int(meta['annotations'][REPLICAS_ANNOTATION_KEY])
    logger.info(
        f"Scaling up deployment to the previous replica count: {replicas}")

    # Scale up the deployment
    return patch_deployment(
        name=meta['name'],
        namespace=meta['namespace'],
        body={
            "spec": {
                "replicas": replicas
            }
        },
        logger=logger
    )


def patch_deployment(name: str, namespace: str, body: Dict, logger):
    """
    Patches the Kubernetes deployment with a given body.
    """
    try:
        v1.patch_namespaced_deployment(
            name=name,
            namespace=namespace,
            body=body
        )
        logger.debug(
            f"Successfully patched deployment {name} in namespace {namespace}")
    except klient.exceptions.ApiException as e:
        logger.error(
            f"Failed to patch deployment {name} in namespace {namespace}: {e}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred while patching deployment: {e}")


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, logger, **_):
    """
    Configures Kopf's operator settings to use a meaningful finalizer name.

    While a finalizer is not strictly necessary for this specific use case,
    Kopf currently doesn't provide a way to disable it. As a result, we
    configure it to use a meaningful name that aligns with our CRD's API group.

    For more details on the limitations of disabling finalizers in Kopf,
    see the GitHub issue: https://github.com/nolar/kopf/issues/1001.
    """
    settings.persistence.finalizer = f'finalizers.{API_GROUP}/workschedule-cleanup'
