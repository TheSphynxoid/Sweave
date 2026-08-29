import React from 'react';

const projects = [{name: 'test', path: '/test'}];

export function Test() {
  return (
    <div>
      {projects.map((project) => (
        <div key={project.name}>{project.name}</div>
      ))}
    </div>
  );
}