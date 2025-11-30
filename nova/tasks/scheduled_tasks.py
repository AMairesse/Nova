# nova/tasks/scheduled_tasks.py
import logging
from django.utils import timezone
from celery import shared_task
from nova.models.ScheduledTask import ScheduledTask
from nova.models.Thread import Thread
from nova.llm.llm_agent import LLMAgent
from nova.models.Message import Actor

logger = logging.getLogger(__name__)


@shared_task
def run_scheduled_agent_task(scheduled_task_id):
    """
    Celery task to execute a scheduled agent task.
    """
    try:
        # Get the scheduled task
        scheduled_task = ScheduledTask.objects.get(id=scheduled_task_id)

        if not scheduled_task.is_active:
            logger.info(f"Scheduled task {scheduled_task.name} is not active, skipping.")
            return

        # Create a new thread for this execution
        thread = Thread.objects.create(
            user=scheduled_task.user,
            subject=scheduled_task.name
        )

        # Create the agent
        agent = LLMAgent.create(
            user=scheduled_task.user,
            thread=thread,
            agent_config=scheduled_task.agent
        )

        # Execute the agent with the prompt
        result = agent.ainvoke(scheduled_task.prompt)

        # Add the result as a message to the thread
        thread.add_message(str(result), Actor.AGENT, "standard")

        # Update last run time
        scheduled_task.last_run_at = timezone.now()
        scheduled_task.last_error = None  # Clear any previous error
        scheduled_task.save()

        # Optionally delete the thread
        if not scheduled_task.keep_thread:
            thread.delete()

        logger.info(f"Scheduled task {scheduled_task.name} executed successfully.")

    except Exception as e:
        logger.error(f"Error executing scheduled task {scheduled_task_id}: {e}", exc_info=True)

        # Update the scheduled task with the error
        try:
            scheduled_task = ScheduledTask.objects.get(id=scheduled_task_id)
            scheduled_task.last_error = str(e)
            scheduled_task.last_run_at = timezone.now()
            scheduled_task.save()
        except Exception as inner_e:
            logger.error(f"Failed to update scheduled task with error: {inner_e}")
