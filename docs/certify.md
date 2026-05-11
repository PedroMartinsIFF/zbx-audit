# KB Alarme de certificados

## Sumário

Este documento orienta sobre os procedimentos a serem realiados em caso de alarmes relacionados a expiração de certificados

## Alertas

**Formato Problema**  
Certificate: failure to check expiration date
**Descrição** 
Não foi possível coletar informações do certificado por 6 horas consecutivas.
**Criticiade** 
Alta
**Procedimento de Resolução**
1.  Efetuar o teste do certificado com o comando "curl" e verificar a data de expiração com o comando: curl -skvo /dev/null https://$URL_DO_CERTIFICADO
2. Acessar o certificado através do Globo Dominios para início da renovação do certificado
3. Verificar se o certificado está como "Renovação automática", caso sim, clique em renovar, caso não, verificar com o responsável se o certificado será renovado
4. Caso não seja renovado automaticamente, clique em "editar" e verifique o email indicado que receberá a aprovação.
5. Ao localizar concluir o pedido de renovação e aguardar o alarme sair da monitoração. 
6. Encerrar o evento
Caso se estogem as soluções de contorno, acionar o time de GD, P2

**Formato Problema**  
Certificate close to expire --> X
**Descrição** 
Certificado proximo da data de expiração, em que X é o numero de dias até a expiração
**Criticiade** 
Depende do número de dias
Se X < 10. Criticidade alta, se X > 10, criticade média
**Procedimento de Resolução**
1.  Efetuar o teste do certificado com o comando "curl" e verificar a data de expiração com o comando: curl -skvo /dev/null https://$URL_DO_CERTIFICADO
2. Acessar o certificado através do Globo Dominios para início da renovação do certificado
3. Verificar se o certificado está como "Renovação automática", caso sim, clique em renovar, caso não, verificar com o responsável se o certificado será renovado
4. Caso não seja renovado automaticamente, clique em "editar" e verifique o email indicado que receberá a aprovação.
5. Ao localizar concluir o pedido de renovação e aguardar o alarme sair da monitoração. 
6. Encerrar o evento
Caso se estogem as soluções de contorno, acionar o time de GD, P2

**Formato Problema**
Certificate expired --> X
**Descrição** 
Certificado expirado, em que X é o numero de dias que ele está expirado
**Criticiade** 
Alta
**Procedimento de Resolução**
1.  Efetuar o teste do certificado com o comando "curl" e verificar a data de expiração com o comando: curl -skvo /dev/null https://$URL_DO_CERTIFICADO
2. Acessar o certificado através do Globo Dominios para início da renovação do certificado
3. Verificar se o certificado está como "Renovação automática", caso sim, clique em renovar, caso não, verificar com o responsável se o certificado será renovado
4. Caso não seja renovado automaticamente, clique em "editar" e verifique o email indicado que receberá a aprovação.
5. Ao localizar concluir o pedido de renovação e aguardar o alarme sair da monitoração. 
6. Encerrar o evento
Caso se estogem as soluções de contorno, acionar o time de GD, P2
