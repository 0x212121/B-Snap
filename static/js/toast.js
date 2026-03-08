/**
 * B-Snap Toast Notification System
 * 
 * A lightweight, customizable toast notification system with WebSocket support.
 * 
 * Usage:
 *   // Show a simple toast
 *   Toast.info('Hello World');
 *   
 *   // Show with options
 *   Toast.success('Saved!', { duration: 3000, position: 'top-center' });
 *   
 *   // Show with actions
 *   Toast.warning('Camera offline', {
 *     actions: [{ label: 'Check', url: '/health' }]
 *   });
 */

(function() {
    'use strict';

    // Configuration
    const CONFIG = {
        defaultDuration: 5000,
        maxToasts: 5,
        animationDuration: 300,
        positions: ['top-right', 'top-left', 'top-center', 'bottom-right', 'bottom-left', 'bottom-center']
    };

    // Toast types with their styles
    const TYPES = {
        success: {
            icon: `<svg class="w-6 h-6 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>`,
            bgClass: 'bg-green-50 dark:bg-green-900/20',
            borderClass: 'border-green-400 dark:border-green-800',
            titleClass: 'text-green-800 dark:text-green-200'
        },
        error: {
            icon: `<svg class="w-6 h-6 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
            </svg>`,
            bgClass: 'bg-red-50 dark:bg-red-900/20',
            borderClass: 'border-red-400 dark:border-red-800',
            titleClass: 'text-red-800 dark:text-red-200'
        },
        warning: {
            icon: `<svg class="w-6 h-6 text-yellow-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
            </svg>`,
            bgClass: 'bg-yellow-50 dark:bg-yellow-900/20',
            borderClass: 'border-yellow-400 dark:border-yellow-800',
            titleClass: 'text-yellow-800 dark:text-yellow-200'
        },
        info: {
            icon: `<svg class="w-6 h-6 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>`,
            bgClass: 'bg-blue-50 dark:bg-blue-900/20',
            borderClass: 'border-blue-400 dark:border-blue-800',
            titleClass: 'text-blue-800 dark:text-blue-200'
        }
    };

    // Store active toasts
    const activeToasts = new Map();
    let toastIdCounter = 0;
    let wsConnection = null;

    /**
     * Create a toast element
     */
    function createToastElement(options) {
        const {
            id,
            message,
            title,
            type = 'info',
            position = 'top-right',
            dismissible = true,
            actions = [],
            persistent = false
        } = options;

        const typeConfig = TYPES[type] || TYPES.info;
        
        const toast = document.createElement('div');
        toast.id = `toast-${id}`;
        toast.className = `
            toast-item fixed z-50 max-w-sm w-full shadow-lg rounded-lg pointer-events-auto 
            transform transition-all duration-${CONFIG.animationDuration} 
            border-l-4 ${typeConfig.borderClass} ${typeConfig.bgClass}
            dark:bg-gray-800 dark:text-white
        `;
        
        // Set initial position for animation
        const isBottom = position.startsWith('bottom');
        toast.style.transform = isBottom ? 'translateY(100%)' : 'translateY(-100%)';
        toast.style.opacity = '0';

        // Build content
        let content = `
            <div class="p-4">
                <div class="flex items-start">
                    <div class="flex-shrink-0">
                        ${typeConfig.icon}
                    </div>
                    <div class="ml-3 w-0 flex-1 pt-0.5">
                        ${title ? `<p class="text-sm font-medium ${typeConfig.titleClass}">${escapeHtml(title)}</p>` : ''}
                        <p class="text-sm text-gray-700 dark:text-gray-300 mt-1">${escapeHtml(message)}</p>
                        
                        ${actions.length > 0 ? `
                            <div class="mt-3 flex space-x-2">
                                ${actions.map(action => `
                                    <a href="${action.url}" 
                                       class="text-sm font-medium text-blue-600 hover:text-blue-500 dark:text-blue-400 dark:hover:text-blue-300">
                                        ${escapeHtml(action.label)}
                                    </a>
                                `).join('')}
                            </div>
                        ` : ''}
                    </div>
                    ${dismissible ? `
                        <div class="ml-4 flex-shrink-0 flex">
                            <button onclick="Toast.dismiss(${id})" 
                                    class="bg-transparent rounded-md inline-flex text-gray-400 hover:text-gray-500 focus:outline-none">
                                <span class="sr-only">Close</span>
                                <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                                </svg>
                            </button>
                        </div>
                    ` : ''}
                </div>
                ${!persistent && dismissible ? `
                    <div class="mt-2 h-1 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                        <div class="toast-progress h-full bg-current opacity-30" style="width: 100%"></div>
                    </div>
                ` : ''}
            </div>
        `;

        toast.innerHTML = content;
        return toast;
    }

    /**
     * Position a toast element
     */
    function positionToast(toast, position, index) {
        const offset = index * 10; // Gap between toasts
        const height = toast.offsetHeight + 16; // Height + margin
        
        const positions = {
            'top-right': { top: `${20 + offset}px`, right: '20px', left: 'auto', bottom: 'auto' },
            'top-left': { top: `${20 + offset}px`, left: '20px', right: 'auto', bottom: 'auto' },
            'top-center': { top: `${20 + offset}px`, left: '50%', transform: 'translateX(-50%)', right: 'auto', bottom: 'auto' },
            'bottom-right': { bottom: `${20 + offset}px`, right: '20px', left: 'auto', top: 'auto' },
            'bottom-left': { bottom: `${20 + offset}px`, left: '20px', right: 'auto', top: 'auto' },
            'bottom-center': { bottom: `${20 + offset}px`, left: '50%', transform: 'translateX(-50%)', right: 'auto', top: 'auto' }
        };

        const pos = positions[position] || positions['top-right'];
        Object.assign(toast.style, pos);
    }

    /**
     * Update positions of all toasts in a position group
     */
    function updatePositions(position) {
        const toasts = document.querySelectorAll(`.toast-item[data-position="${position}"]`);
        toasts.forEach((toast, index) => {
            positionToast(toast, position, index);
        });
    }

    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Generate unique ID
     */
    function generateId() {
        return ++toastIdCounter;
    }

    /**
     * Show a toast notification
     */
    function show(options) {
        // Check max toasts
        if (activeToasts.size >= CONFIG.maxToasts) {
            // Remove oldest toast
            const oldestId = activeToasts.keys().next().value;
            dismiss(oldestId);
        }

        const id = options.id || generateId();
        const position = options.position || 'top-right';
        
        const toast = createToastElement({ ...options, id, position });
        toast.setAttribute('data-position', position);
        document.body.appendChild(toast);

        // Store toast data
        activeToasts.set(id, {
            element: toast,
            timeout: null,
            startTime: Date.now(),
            duration: options.duration || CONFIG.defaultDuration,
            persistent: options.persistent || false
        });

        // Position toast
        positionToast(toast, position, document.querySelectorAll(`.toast-item[data-position="${position}"]`).length - 1);

        // Animate in
        requestAnimationFrame(() => {
            toast.style.transform = position.startsWith('bottom') ? 'translateY(0)' : 'translateY(0)';
            toast.style.opacity = '1';
        });

        // Start progress bar animation if not persistent
        if (!options.persistent && options.duration !== 0) {
            const progressBar = toast.querySelector('.toast-progress');
            if (progressBar) {
                const duration = options.duration || CONFIG.defaultDuration;
                progressBar.style.transition = `width ${duration}ms linear`;
                requestAnimationFrame(() => {
                    progressBar.style.width = '0%';
                });
            }
        }

        // Auto dismiss
        if (!options.persistent && options.duration !== 0) {
            const timeout = setTimeout(() => {
                dismiss(id);
            }, options.duration || CONFIG.defaultDuration);
            
            activeToasts.get(id).timeout = timeout;
        }

        return id;
    }

    /**
     * Dismiss a toast
     */
    function dismiss(id) {
        const toastData = activeToasts.get(id);
        if (!toastData) return;

        const { element, timeout } = toastData;
        
        // Clear timeout
        if (timeout) clearTimeout(timeout);

        // Animate out
        const position = element.getAttribute('data-position');
        const isBottom = position.startsWith('bottom');
        element.style.transform = isBottom ? 'translateY(100%)' : 'translateY(-100%)';
        element.style.opacity = '0';

        // Remove after animation
        setTimeout(() => {
            element.remove();
            activeToasts.delete(id);
            // Update positions for remaining toasts
            updatePositions(position);
        }, CONFIG.animationDuration);
    }

    /**
     * Dismiss all toasts
     */
    function dismissAll() {
        activeToasts.forEach((_, id) => dismiss(id));
    }

    /**
     * Show toast from WebSocket message
     */
    function handleWebSocketMessage(data) {
        if (data.type === 'toast') {
            show(data.data);
        }
    }

    /**
     * Initialize WebSocket connection
     */
    function initWebSocket() {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

        try {
            wsConnection = new WebSocket(wsUrl);

            wsConnection.onopen = () => {
                console.log('Toast: WebSocket connected');
            };

            wsConnection.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    handleWebSocketMessage(data);
                } catch (e) {
                    console.error('Toast: Failed to parse WebSocket message', e);
                }
            };

            wsConnection.onclose = () => {
                console.log('Toast: WebSocket disconnected, retrying in 5s...');
                setTimeout(initWebSocket, 5000);
            };

            wsConnection.onerror = (error) => {
                console.error('Toast: WebSocket error', error);
            };
        } catch (e) {
            console.error('Toast: Failed to connect WebSocket', e);
        }
    }

    /**
     * Show success toast
     */
    function success(message, options = {}) {
        return show({ ...options, message, type: 'success' });
    }

    /**
     * Show error toast
     */
    function error(message, options = {}) {
        return show({ ...options, message, type: 'error' });
    }

    /**
     * Show warning toast
     */
    function warning(message, options = {}) {
        return show({ ...options, message, type: 'warning' });
    }

    /**
     * Show info toast
     */
    function info(message, options = {}) {
        return show({ ...options, message, type: 'info' });
    }

    /**
     * Show camera offline notification
     */
    function cameraOffline(cameraName, cameraId) {
        return error(`Camera '${cameraName}' is offline`, {
            title: 'Camera Offline',
            actions: [{ label: 'Check Status', url: '/health' }],
            duration: 8000
        });
    }

    /**
     * Show camera online notification
     */
    function cameraOnline(cameraName, cameraId) {
        return success(`Camera '${cameraName}' is back online`, {
            title: 'Camera Online',
            duration: 4000
        });
    }

    /**
     * Show snapshot saved notification
     */
    function snapshotSaved(cameraName, cameraId, snapshotId) {
        return success(`Snapshot saved from '${cameraName}'`, {
            title: 'Snapshot Captured',
            actions: [{ label: 'View', url: `/snapshots/${snapshotId}` }],
            duration: 4000
        });
    }

    // Public API
    window.Toast = {
        show,
        dismiss,
        dismissAll,
        success,
        error,
        warning,
        info,
        cameraOffline,
        cameraOnline,
        snapshotSaved,
        initWebSocket,
        getConnection: () => wsConnection
    };

    // Initialize WebSocket on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initWebSocket);
    } else {
        initWebSocket();
    }

})();
