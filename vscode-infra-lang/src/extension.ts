import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
} from 'vscode-languageclient/node';

let client: LanguageClient;

export function activate(context: vscode.ExtensionContext): void {
    const pythonPath = vscode.workspace
        .getConfiguration('python')
        .get<string>('defaultInterpreterPath', 'python');

    const serverOptions: ServerOptions = {
        command: pythonPath,
        args: ['-m', 'infra.lsp.server'],
        transport: TransportKind.stdio,
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [{ scheme: 'file', language: 'infra' }],
        synchronize: {
            fileEvents: vscode.workspace.createFileSystemWatcher('**/*.infra'),
        },
    };

    client = new LanguageClient(
        'infra-lang',
        'Infra Lang',
        serverOptions,
        clientOptions,
    );

    client.start();
    context.subscriptions.push(client);
}

export function deactivate(): Promise<void> | undefined {
    return client?.stop();
}
