import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, Subject, WebSocketSubject } from 'rxjs';
import { webSocket } from 'rxjs/webSocket';
import { catchError, tap } from 'rxjs/operators';

interface QueryRequest {
  query: string;
  limit?: number;
  query_type?: string;
  half_life?: number;
  similarity_weight?: number;
  freshness_weight?: number;
}

interface QueryResponse {
  query: string;
  query_type_detected: string;
  similarity_weight: number;
  freshness_weight: number;
  answer: string;
  evidence: any[];
}

@Injectable({
  providedIn: 'root'
})
export class RagService {
  private apiBaseUrl = 'http://localhost:8000';
  private wsBaseUrl = 'ws://localhost:8000';
  private wsSubject: WebSocketSubject<any> | null = null;
  private messageSubject = new Subject<any>();

  constructor(private http: HttpClient) {}

  /**
   * Send a REST query to the backend
   */
  query(request: QueryRequest): Observable<QueryResponse> {
    return this.http.post<QueryResponse>(`${this.apiBaseUrl}/query`, request).pipe(
      tap(response => {
        console.log('Query response:', response);
      }),
      catchError(error => {
        console.error('Query error:', error);
        throw error;
      })
    );
  }

  /**
   * Subscribe to WebSocket updates for live results
   */
  subscribe(request: QueryRequest): Observable<any> {
    // Close any existing WebSocket connection
    if (this.wsSubject) {
      this.wsSubject.complete();
      this.wsSubject = null;
    }

    return new Observable(observer => {
      try {
        // Create WebSocket connection
        this.wsSubject = webSocket({
          url: `${this.wsBaseUrl}/subscribe`,
          openObserver: {
            next: () => {
              console.log('WebSocket connection opened');
              // Send subscription setup message
              this.wsSubject!.next({
                query: request.query,
                limit: request.limit || 5,
                query_type: request.query_type,
                half_life: request.half_life || 3600.0,
                similarity_weight: request.similarity_weight,
                freshness_weight: request.freshness_weight
              });
            }
          },
          closeObserver: {
            next: () => {
              console.log('WebSocket connection closed');
              observer.complete();
            }
          }
        });

        // Subscribe to WebSocket messages
        this.wsSubject.pipe(
          tap(message => {
            console.log('WebSocket message:', message);
            observer.next(message);
          }),
          catchError(error => {
            console.error('WebSocket error:', error);
            observer.error(error);
            return [];
          })
        ).subscribe();

        // Return unsubscribe function
        return () => {
          if (this.wsSubject) {
            this.wsSubject.complete();
            this.wsSubject = null;
          }
        };
      } catch (error: any) {
        console.error('WebSocket setup error:', error);
        observer.error(error);
        return () => {};
      }
    });
  }

  /**
   * Close WebSocket connection
   */
  closeWebSocket(): void {
    if (this.wsSubject) {
      this.wsSubject.complete();
      this.wsSubject = null;
    }
  }

  /**
   * Check backend health
   */
  health(): Observable<any> {
    return this.http.get(`${this.apiBaseUrl}/health`).pipe(
      catchError(error => {
        console.error('Health check error:', error);
        throw error;
      })
    );
  }
}