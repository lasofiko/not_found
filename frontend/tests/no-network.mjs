import http from 'node:http'
import https from 'node:https'
import net from 'node:net'
import tls from 'node:tls'

const deny = () => { throw new Error('Внешняя сеть запрещена в обычных frontend-тестах. Подмените transport.') }
globalThis.fetch = deny
http.request = http.get = https.request = https.get = deny
net.connect = net.createConnection = tls.connect = deny
