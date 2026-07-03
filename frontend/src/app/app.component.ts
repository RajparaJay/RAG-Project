import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { RagService } from './services/rag.service';

interface EvidenceItem {
  id: number;
  event_id: string;
  event_type: string;
  actor: string;
  repo: string;
  content: string;
  created_at: string;
  similarity_score: number;
  freshness_score: number;
  hybrid_score: number;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, HttpClientModule],
  providers: [RagService],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css'
})
export class AppComponent implements OnInit, OnDestroy {
  // Search form state
  searchQuery: string = '';
  searchMode: 'query' | 'subscribe' = 'query';
  resultLimit: number = 5;
  halfLife: number = 3600;

  // Results state
  hasResults: boolean = false;
  isLoading: boolean = false;
  isStreaming: boolean = false;
  answer: string = '';
  evidence: EvidenceItem[] = [];
  queryTypeDetected: string = '';
  similarityWeight: number = 0.9;
  freshnessWeight: number = 0.1;

  // Messages
  errorMessage: string = '';
  successMessage: string = '';

  // WebSocket subscription
  private wsSubscription: any = null;
  private messageTimeout: any = null;

  constructor(private ragService: RagService) {}

  ngOnInit(): void {
    // Initialize any required services
  }

  ngOnDestroy(): void {
    this.closeWebSocket();
    if (this.messageTimeout) {
      clearTimeout(this.messageTimeout);
    }
  }

  async performSearch(): Promise<void> {
    // Validation
    if (!this.searchQuery.trim()) {
      this.showError('Please enter a search query');
      return;
    }

    this.clearMessages();
    this.isLoading = true;

    try {
      if (this.searchMode === 'query') {
        await this.performRestQuery();
      } else {
        await this.performWebSocketSubscription();
      }
    } catch (error: any) {
      this.showError(error.message || 'An error occurred during search');
    } finally {
      this.isLoading = false;
    }
  }

  private async performRestQuery(): Promise<void> {
    try {
      const response = await this.ragService.query({
        query: this.searchQuery,
        limit: this.resultLimit,
        half_life: this.halfLife
      }).toPromise();

      if (response) {
        this.updateResults(response);
        this.hasResults = true;
        this.isStreaming = false;
        this.showSuccess('Search completed');
      }
    } catch (error: any) {
      throw new Error(`REST query failed: ${error.message}`);
    }
  }

  private async performWebSocketSubscription(): Promise<void> {
    try {
      this.closeWebSocket(); // Close any existing subscription

      this.isStreaming = true;
      this.ragService.subscribe({
        query: this.searchQuery,
        limit: this.resultLimit,
        half_life: this.halfLife
      }).subscribe({
        next: (message: any) => {
          if (message.type === 'subscribed') {
            this.showSuccess(`Subscribed to: ${message.query}`);
          } else if (message.type === 'update') {
            this.updateResults(message);
            this.hasResults = true;
          } else if (message.type === 'error') {
            this.showError(message.message);
          }
        },
        error: (error: any) => {
          this.showError(`WebSocket error: ${error.message}`);
          this.isStreaming = false;
        },
        complete: () => {
          this.isStreaming = false;
        }
      });
    } catch (error: any) {
      throw new Error(`WebSocket subscription failed: ${error.message}`);
    }
  }

  private updateResults(response: any): void {
    this.answer = response.answer || '';
    this.evidence = response.evidence || [];
    this.queryTypeDetected = response.query_type_detected || 'CONCEPTUAL';
    this.similarityWeight = response.similarity_weight || 0.9;
    this.freshnessWeight = response.freshness_weight || 0.1;
  }

  formatTime(isoString: string): string {
    try {
      const date = new Date(isoString);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      const diffHours = Math.floor(diffMs / 3600000);
      const diffDays = Math.floor(diffMs / 86400000);

      if (diffMins < 1) return 'just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      if (diffHours < 24) return `${diffHours}h ago`;
      if (diffDays < 7) return `${diffDays}d ago`;

      return date.toLocaleDateString();
    } catch {
      return isoString;
    }
  }

  private closeWebSocket(): void {
    if (this.wsSubscription) {
      this.wsSubscription.unsubscribe();
      this.wsSubscription = null;
    }
  }

  private showError(message: string): void {
    this.errorMessage = message;
    this.successMessage = '';
    if (this.messageTimeout) clearTimeout(this.messageTimeout);
    this.messageTimeout = setTimeout(() => {
      this.errorMessage = '';
    }, 5000);
  }

  private showSuccess(message: string): void {
    this.successMessage = message;
    this.errorMessage = '';
    if (this.messageTimeout) clearTimeout(this.messageTimeout);
    this.messageTimeout = setTimeout(() => {
      this.successMessage = '';
    }, 3000);
  }

  private clearMessages(): void {
    this.errorMessage = '';
    this.successMessage = '';
  }
}
