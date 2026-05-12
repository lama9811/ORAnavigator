// src/components/Forbidden.jsx
import React from 'react';

export default function Forbidden() {
  return (
    <div style={{ padding: '2rem', textAlign: 'center' }}>
      <h1>403: Access Denied</h1>
      <p>You do not have permission to view this page.</p>
      <button
        onClick={() => window.history.back()}
        style={{
          marginTop: '1rem',
          padding: '0.5rem 1rem',
          backgroundColor: '#4A90E2',
          color: '#fff',
          border: 'none',
          borderRadius: '4px',
          cursor: 'pointer'
        }}
      >
        Go Back
      </button>
    </div>
  );
}
