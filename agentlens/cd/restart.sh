#!/bin/bash

SERVICE_NAME="app"

sc stop $SERVICE_NAME
sc start $SERVICE_NAME
