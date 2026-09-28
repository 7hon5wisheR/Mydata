import React from 'react';

const StatusButton = ({ onClick, label }) => {
  return (
    <button className="statusButton" onClick={onClick}>
      {label}
    </button>
  );
};

export default StatusButton;
