// Drag and drop functionality for reordering groups
(function() {
    let draggedElement = null;
    let draggedIndex = null;
    let currentDropTarget = null;

    function initializeDragAndDrop() {
        const groupCards = document.querySelectorAll('.group-card');
        
        groupCards.forEach((card, index) => {
            card.setAttribute('draggable', 'true');
            card.dataset.index = index;
            
            card.addEventListener('dragstart', handleDragStart);
            card.addEventListener('dragover', handleDragOver);
            card.addEventListener('drop', handleDrop);
            card.addEventListener('dragend', handleDragEnd);
        });
    }

    function handleDragStart(e) {
        draggedElement = this;
        draggedIndex = parseInt(this.dataset.index);
        
        this.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/html', this.innerHTML);
    }

    function handleDragOver(e) {
        if (e.preventDefault) {
            e.preventDefault();
        }
        e.dataTransfer.dropEffect = 'move';
        
        // Find the closest group card
        const card = e.target.closest('.group-card');
        
        if (card && card !== draggedElement) {
            // Remove highlight from previous target
            if (currentDropTarget && currentDropTarget !== card) {
                currentDropTarget.classList.remove('drag-over');
            }
            
            // Add highlight to current target
            card.classList.add('drag-over');
            currentDropTarget = card;
        }
        
        return false;
    }

    function handleDrop(e) {
        if (e.stopPropagation) {
            e.stopPropagation();
        }

        const card = e.target.closest('.group-card');
        
        if (card) {
            card.classList.remove('drag-over');
        }

        if (draggedElement !== card && card && card.classList.contains('group-card')) {
            const targetIndex = parseInt(card.dataset.index);
            const cardGrid = document.querySelector('.card-grid');
            
            // Reorder in DOM
            if (draggedIndex < targetIndex) {
                card.parentNode.insertBefore(draggedElement, card.nextSibling);
            } else {
                card.parentNode.insertBefore(draggedElement, card);
            }
            
            // Update indices
            updateIndices();
            
            // Save new order to server
            saveOrder();
        }

        return false;
    }

    function handleDragEnd(e) {
        this.classList.remove('dragging');
        
        // Remove drag-over class from all cards
        const groupCards = document.querySelectorAll('.group-card');
        groupCards.forEach(card => {
            card.classList.remove('drag-over');
        });
        
        currentDropTarget = null;
    }

    function updateIndices() {
        const groupCards = document.querySelectorAll('.group-card');
        groupCards.forEach((card, index) => {
            card.dataset.index = index;
        });
    }

    function saveOrder() {
        const groupCards = document.querySelectorAll('.group-card');
        const groupIds = Array.from(groupCards).map(card => parseInt(card.dataset.groupId));
        
        fetch('/groups/reorder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ group_ids: groupIds })
        })
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                console.error('Failed to save order:', data.error);
                // Optionally show error to user
            }
        })
        .catch(error => {
            console.error('Error saving order:', error);
        });
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeDragAndDrop);
    } else {
        initializeDragAndDrop();
    }
})();
