// static/nova/js/notification-manager.js
(function () {
    'use strict';

    function isAuthenticated() {
        return document.body?.dataset?.authenticated === 'true';
    }

    function isSupported() {
        return (
            'Notification' in window &&
            'serviceWorker' in navigator &&
            'PushManager' in window
        );
    }

    function shouldPromptFromCurrentPage() {
        return Boolean(
            document.getElementById('message-container') ||
            document.getElementById('continuous-page-root')
        );
    }

    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    window.NotificationManager = {
        registration: null,
        config: null,

        async init(registration) {
            if (!isAuthenticated() || !isSupported()) {
                return;
            }
            this.registration = registration;

            const config = await this.fetchConfig();
            this.config = config;
            if (!config) return;

            document.dispatchEvent(new CustomEvent('nova:push-config', { detail: config }));

            if (config.server_state !== 'ready') {
                return;
            }

            if (!config.user_opt_in) {
                await this.unsubscribe();
                return;
            }

            if (Notification.permission === 'granted') {
                await this.syncSubscription();
                return;
            }

            if (Notification.permission === 'default' && shouldPromptFromCurrentPage()) {
                await this.requestPermissionAndSubscribe();
            }
        },

        async fetchConfig() {
            try {
                const response = await fetch('/push/config/', {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                if (!response.ok) return null;
                return await response.json();
            } catch (error) {
                console.warn('Failed to fetch push config:', error);
                return null;
            }
        },

        async requestPermissionAndSubscribe() {
            if (!this.config || this.config.server_state !== 'ready') {
                return false;
            }
            try {
                const permission = await Notification.requestPermission();
                if (permission !== 'granted') {
                    return false;
                }
                await this.syncSubscription();
                return true;
            } catch (error) {
                console.warn('Push permission request failed:', error);
                return false;
            }
        },

        async syncSubscription() {
            if (!this.registration || !this.config || !this.config.vapid_public_key) {
                return false;
            }

            try {
                let subscription = await this.registration.pushManager.getSubscription();
                if (!subscription) {
                    subscription = await this.registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(this.config.vapid_public_key),
                    });
                }

                if (!window.DOMUtils?.csrfFetch) {
                    return false;
                }
                const response = await window.DOMUtils.csrfFetch('/push/subscriptions/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(subscription.toJSON()),
                });
                return response.ok;
            } catch (error) {
                console.warn('Failed to sync push subscription:', error);
                return false;
            }
        },

        async unsubscribe() {
            if (!this.registration) {
                return false;
            }

            try {
                const subscription = await this.registration.pushManager.getSubscription();
                if (!subscription) return true;
                const endpoint = subscription.endpoint;
                await subscription.unsubscribe();

                if (endpoint && window.DOMUtils?.csrfFetch) {
                    await window.DOMUtils.csrfFetch('/push/subscriptions/', {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ endpoint }),
                    });
                }
                return true;
            } catch (error) {
                console.warn('Failed to unsubscribe push subscription:', error);
                return false;
            }
        },
    };
})();
