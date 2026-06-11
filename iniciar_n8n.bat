@echo off
REM Lanzador de n8n para el Agente de Empleo
REM NODES_EXCLUDE=[] habilita el nodo Execute Command (bloqueado por defecto en n8n v2)
set NODES_EXCLUDE=[]
echo Iniciando n8n... no cierres esta ventana (es el servidor).
echo Editor: http://localhost:5678
n8n
