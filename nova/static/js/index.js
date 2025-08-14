/* nova/static/js/index.js */
(function() {
  'use strict';

  // Configuration object for URLs (will be populated from template)
  window.NovaApp = window.NovaApp || {};
  
  // Thread management functionality
  const ThreadManager = {
    init() {
      this.attachEventHandlers();
      this.loadInitialThread();
    },

    markActiveThread(threadId) {
      // Remove .active from the current thread
      document.querySelectorAll('.thread-link.active')
              .forEach(el => el.classList.remove('active'));

      // Set active on the new selected one
      if (threadId) {
        document.querySelector(`.thread-link[data-thread-id="${threadId}"]`)
                ?.classList.add('active');
      }
    },

    attachEventHandlers() {
      // Create thread button
      document.querySelector('.create-thread-btn')?.addEventListener('click', this.createThread.bind(this));
      
      // Thread click and delete handlers (delegated)
      document.addEventListener('click', (e) => {
        if (e.target.matches('.thread-link') || e.target.closest('.thread-link')) {
          e.preventDefault();
          const link = e.target.matches('.thread-link') ? e.target : e.target.closest('.thread-link');
          const threadId = link.dataset.threadId;
          this.loadMessages(threadId);
        }
        
        if (e.target.matches('.delete-thread-btn') || e.target.closest('.delete-thread-btn')) {
          e.preventDefault();
          const btn = e.target.matches('.delete-thread-btn') ? e.target : e.target.closest('.delete-thread-btn');
          const threadId = btn.dataset.threadId;
          this.deleteThread(threadId);
        }
      });
    },

    async createThread() {
      try {
        const token = await window.getCSRFToken();
        const response = await fetch(window.NovaApp.urls.createThread, {
          method: 'POST',
          headers: {
            'X-AJAX': 'true',
            'X-CSRFToken': token
          }
        });
        
        const data = await response.json();
        
        // Add new thread to list
        const threadList = document.querySelector('.list-group');
        threadList.insertAdjacentHTML('afterbegin', data.threadHtml);
        
        // Load messages for new thread
        this.loadMessages(data.thread_id);
      } catch (error) {
        console.error('Error creating thread:', error);
      }
    },

    async loadMessages(threadId) {
      try {
        const params = threadId ? `?thread_id=${threadId}` : '';
        const response = await fetch(`${window.NovaApp.urls.messageList}${params}`, {
          headers: { 'X-AJAX': 'true' }
        });
        
        const html = await response.text();
        document.getElementById('message-container').innerHTML = html;
        this.markActiveThread(threadId);

        // Initialize message container
        if (typeof window.initMessageContainer === 'function') {
          window.initMessageContainer();
        }

        // Handle running tasks if threadId exists
        if (threadId) {
          await this.handleRunningTasks(threadId);
          localStorage.setItem('lastThreadId', threadId);
        }
      } catch (error) {
        console.error('Error loading messages:', error);
      }
    },

    async handleRunningTasks(threadId) {
      try {
        const response = await fetch(`${window.NovaApp.urls.runningTasksBase}${threadId}/`, {
          headers: { 'X-AJAX': 'true' }
        });
        
        const data = await response.json();
        const runningTasks = data.running_task_ids || [];
        
        // Fallback to stored tasks if API empty
        const storedTasks = this.getStoredRunningTasks(threadId);
        const tasksToResume = runningTasks.length > 0 ? runningTasks : storedTasks;

        if (tasksToResume.length > 0) {
          const progressEl = document.getElementById('task-progress');
          if (progressEl) progressEl.style.display = 'block';
          
          tasksToResume.forEach(taskId => {
            if (typeof window.startTaskWebSocket === 'function') {
              window.startTaskWebSocket(threadId, taskId);
            }
          });
        }
      } catch (error) {
        console.error('Error fetching running tasks:', error);
      }
    },

    async deleteThread(threadId) {
      try {
        const token = await window.getCSRFToken();
        const url = window.NovaApp.urls.deleteThread.replace('0', threadId);
        
        await fetch(url, {
          method: 'POST',
          headers: {
            'X-AJAX': 'true',
            'X-CSRFToken': token
          }
        });

        // Remove thread from DOM
        const threadElement = document.getElementById(`thread-item-${threadId}`);
        if (threadElement) threadElement.remove();

        // Load first available thread
        const firstThread = document.querySelector('.thread-link');
        const firstThreadId = firstThread?.dataset.threadId;
        this.loadMessages(firstThreadId);

        // Clean up localStorage
        localStorage.removeItem(`runningTasks_${threadId}`);
        if (localStorage.getItem('lastThreadId') === threadId.toString()) {
          localStorage.removeItem('lastThreadId');
        }
      } catch (error) {
        console.error('Error deleting thread:', error);
      }
    },

    loadInitialThread() {
      const lastThreadId = localStorage.getItem('lastThreadId');
      this.loadMessages(lastThreadId);
    },

    // Use centralized storage utilities
    getStoredRunningTasks(threadId) {
      return window.StorageUtils.getStoredRunningTasks(threadId);
    }
  };

  // Expose attachEventHandlers globally for compatibility
  window.attachThreadEventHandlers = function() {
    // This is now handled automatically by event delegation in ThreadManager
    // but we keep this for backward compatibility
  };

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ThreadManager.init());
  } else {
    ThreadManager.init();
  }

})();
