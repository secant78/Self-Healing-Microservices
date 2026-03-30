'use strict';

// Must be required first to instrument all subsequent modules
require('./tracing');

const express = require('express');
const axios = require('axios');
const path = require('path');
const { trace, context, propagation } = require('@opentelemetry/api');

const app = express();
const PORT = process.env.PORT || 3000;
const ORDER_SERVICE_URL = process.env.ORDER_SERVICE_URL || 'http://order-service:3001';

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Initialize Application Insights if connection string is provided
if (process.env.APPLICATIONINSIGHTS_CONNECTION_STRING) {
  const appInsights = require('applicationinsights');
  appInsights
    .setup(process.env.APPLICATIONINSIGHTS_CONNECTION_STRING)
    .setAutoDependencyCorrelation(true)
    .setAutoCollectRequests(true)
    .setAutoCollectPerformance(true)
    .setAutoCollectExceptions(true)
    .setAutoCollectDependencies(true)
    .setAutoCollectConsole(true)
    .setUseDiskRetryCaching(true)
    .start();
  console.log('Application Insights initialized');
}

// GET / — serve the main UI
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// GET /health — liveness/readiness probe
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    service: 'frontend',
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
  });
});

// POST /buy — proxy to Order Service with trace context propagation
app.post('/buy', async (req, res) => {
  const startTime = Date.now();
  const tracer = trace.getTracer('frontend');

  const span = tracer.startSpan('buy.request');

  await context.with(trace.setSpan(context.active(), span), async () => {
    try {
      const { productId, quantity } = req.body;

      if (!productId || !quantity) {
        span.setStatus({ code: 2, message: 'Missing productId or quantity' });
        span.end();
        return res.status(400).json({ error: 'productId and quantity are required' });
      }

      span.setAttributes({
        'order.product_id': productId,
        'order.quantity': quantity,
        'order.service_url': ORDER_SERVICE_URL,
      });

      // Inject W3C traceparent + tracestate headers for downstream propagation
      const outgoingHeaders = {};
      propagation.inject(context.active(), outgoingHeaders);

      // Also forward any incoming traceparent from the browser if present
      if (req.headers['traceparent']) {
        outgoingHeaders['traceparent'] = req.headers['traceparent'];
      }

      const response = await axios.post(
        `${ORDER_SERVICE_URL}/order`,
        { productId, quantity },
        {
          headers: {
            'Content-Type': 'application/json',
            ...outgoingHeaders,
          },
          timeout: 10000,
        }
      );

      const latencyMs = Date.now() - startTime;
      const activeSpan = trace.getActiveSpan();
      const traceId = activeSpan
        ? activeSpan.spanContext().traceId
        : 'unavailable';

      span.setAttributes({
        'http.status_code': response.status,
        'order.latency_ms': latencyMs,
        'order.trace_id': traceId,
      });

      span.setStatus({ code: 1 }); // OK
      span.end();

      return res.json({
        success: true,
        traceId,
        latencyMs,
        order: response.data,
      });
    } catch (error) {
      const latencyMs = Date.now() - startTime;
      const activeSpan = trace.getActiveSpan();
      const traceId = activeSpan
        ? activeSpan.spanContext().traceId
        : 'unavailable';

      const statusCode = error.response?.status || 500;
      const errorMessage = error.response?.data?.error || error.message || 'Unknown error';

      span.recordException(error);
      span.setAttributes({
        'http.status_code': statusCode,
        'order.latency_ms': latencyMs,
        'error.message': errorMessage,
        'order.trace_id': traceId,
      });
      span.setStatus({ code: 2, message: errorMessage }); // ERROR
      span.end();

      console.error(`[/buy] Error calling order service: ${errorMessage}`, {
        statusCode,
        traceId,
        latencyMs,
      });

      return res.status(statusCode).json({
        success: false,
        traceId,
        latencyMs,
        error: errorMessage,
        chaosActive: error.response?.data?.chaosActive || false,
      });
    }
  });
});

app.listen(PORT, () => {
  console.log(`Frontend service listening on port ${PORT}`);
  console.log(`Order Service URL: ${ORDER_SERVICE_URL}`);
});
